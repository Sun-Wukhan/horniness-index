"""Shared plumbing for the Horniness Index Kafka pipeline.

Topics:
  headlines.raw    every headline a producer found, keyed by CEO name
  headlines.clean  deduplicated stories (syndicated copies collapsed into a
                   reach count), keyed by CEO name
  ceo.scores       one scored record per CEO, keyed by CEO name

The scoring/rendering core lives in pipeline-index.py (the standalone,
no-Kafka edition). This package is the streaming edition around it: same
rubric, same page, but headlines arrive from multiple producers, travel
through Kafka, and are deduplicated and aggregated as a stream.

Transport abstraction: stages talk to a Transport, not to Kafka directly.
  - KafkaTransport: a real broker (Redpanda in CI, Apache Kafka locally) —
    this is the production path.
  - FileTransport: topics as append-only JSONL files. It exists so the stage
    logic can be exercised end-to-end on machines where a JVM broker cannot
    run; it is a test harness, never the CI path.
"""

import importlib.util
import json
import os
import sys
import time

TOPIC_RAW = "headlines.raw"
TOPIC_CLEAN = "headlines.clean"
TOPIC_SCORES = "ceo.scores"
TOPICS = [TOPIC_RAW, TOPIC_CLEAN, TOPIC_SCORES]


def load_core():
    """Import pipeline-index.py as a module (CEOS, scoring, rendering)."""
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, "..", "horniness-index.py")
    spec = importlib.util.spec_from_file_location("horniness_core", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def make_transport(kind=None, bootstrap=None, topic_dir=None):
    kind = (kind or os.environ.get("HORNINESS_TRANSPORT") or "kafka").lower()
    if kind == "file":
        return FileTransport(topic_dir or os.environ.get("HORNINESS_TOPIC_DIR", ".horniness-topics"))
    return KafkaTransport(bootstrap or os.environ.get("KAFKA_BOOTSTRAP", "localhost:9092"))


class FileTransport:
    """Topics as JSONL files — local test harness for the stage logic."""

    def __init__(self, topic_dir):
        self.dir = topic_dir
        os.makedirs(topic_dir, exist_ok=True)
        self._produced = []

    def _path(self, topic):
        return os.path.join(self.dir, topic + ".jsonl")

    def produce(self, topic, key, value):
        with open(self._path(topic), "a", encoding="utf-8") as f:
            f.write(json.dumps({"key": key, "value": value}) + "\n")
        self._produced.append(topic)

    def flush(self):
        pass

    def consume_all(self, topic, group=None, idle_seconds=0):
        path = self._path(topic)
        if not os.path.exists(path):
            return []
        out = []
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    out.append(json.loads(line)["value"])
        return out

    def close(self):
        pass


class KafkaTransport:
    """Real Kafka/Redpanda transport (kafka-python-ng).

    The producer is created LAZILY, on first use. KafkaProducer's
    constructor connects and probes the broker's API version immediately,
    so constructing it before the broker is listening kills the run with
    kafka-python's infamous `ValueError: Invalid file object: None`
    (learned from CI run 42832665963, where Redpanda needed a few more
    seconds). wait_ready() must pass before anything touches the producer.
    api_version is pinned for the same reason: version probing during a
    broker's start-up window is exactly what breaks.
    """

    API_VERSION = (3, 5, 0)

    def __init__(self, bootstrap):
        self.bootstrap = bootstrap
        self._producer = None

    def _ensure_producer(self):
        if self._producer is None:
            from kafka import KafkaProducer
            self._producer = KafkaProducer(
                bootstrap_servers=self.bootstrap,
                api_version=self.API_VERSION,
                value_serializer=lambda v: json.dumps(v).encode("utf-8"),
                key_serializer=lambda k: k.encode("utf-8") if k else None,
                acks="all",
                retries=5,
            )
        return self._producer

    def ensure_topics(self, partitions=8):
        from kafka.admin import KafkaAdminClient, NewTopic
        from kafka.errors import TopicAlreadyExistsError
        admin = KafkaAdminClient(bootstrap_servers=self.bootstrap)
        try:
            admin.create_topics([NewTopic(t, partitions, 1) for t in TOPICS])
        except TopicAlreadyExistsError:
            pass
        finally:
            admin.close()

    def wait_ready(self, timeout=120):
        """Block until the broker answers metadata requests."""
        from kafka import KafkaConsumer
        deadline = time.time() + timeout
        last = None
        while time.time() < deadline:
            try:
                c = KafkaConsumer(bootstrap_servers=self.bootstrap,
                                  api_version=self.API_VERSION,
                                  consumer_timeout_ms=2000)
                c.topics()
                c.close()
                return True
            except Exception as exc:  # broker still starting
                last = exc
                time.sleep(3)
        raise RuntimeError(f"broker at {self.bootstrap} not ready: {last}")

    def produce(self, topic, key, value):
        self._ensure_producer().send(topic, key=key, value=value)

    def flush(self):
        if self._producer is not None:
            self._producer.flush(timeout=60)

    def consume_all(self, topic, group=None, idle_seconds=10):
        """Batch-drain a topic: read from the beginning until it has been
        quiet for idle_seconds, then return everything. The pipeline is a
        batch job wearing a streaming architecture, by design."""
        from kafka import KafkaConsumer
        consumer = KafkaConsumer(
            topic,
            bootstrap_servers=self.bootstrap,
            api_version=self.API_VERSION,
            group_id=group or f"horniness-{topic}-{int(time.time())}",
            auto_offset_reset="earliest",
            enable_auto_commit=True,
            consumer_timeout_ms=idle_seconds * 1000,
            value_deserializer=lambda b: json.loads(b.decode("utf-8")),
        )
        out = []
        try:
            for msg in consumer:
                out.append(msg.value)
        except Exception:
            pass
        finally:
            consumer.close()
        return out

    def close(self):
        if self._producer is not None:
            try:
                self._producer.close(timeout=10)
            except Exception:
                pass


def stage_banner(name):
    print(f"=== horniness pipeline stage: {name} ===", flush=True)


def transport_from_args(argv):
    """Tiny shared CLI: --transport kafka|file --bootstrap host:port --topic-dir dir"""
    kind, bootstrap, topic_dir = None, None, None
    args = list(argv)
    while args:
        a = args.pop(0)
        if a == "--transport":
            kind = args.pop(0)
        elif a == "--bootstrap":
            bootstrap = args.pop(0)
        elif a == "--topic-dir":
            topic_dir = args.pop(0)
    return make_transport(kind, bootstrap, topic_dir)
