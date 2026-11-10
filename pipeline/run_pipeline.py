#!/usr/bin/env python3
"""Run the whole Horniness Index Kafka pipeline as one batch job.

  producers -> headlines.raw -> dedupe -> headlines.clean
            -> scorer -> ceo.scores -> aggregator -> site

Usage:
  python3 pipeline/run_pipeline.py --transport kafka \
      --bootstrap localhost:9092 --out horniness-site
  python3 pipeline/run_pipeline.py --transport file \
      --topic-dir .horniness-topics --out horniness-site   (local test mode)

In CI the broker is a Redpanda container started by the workflow; locally,
docker compose (docker-compose.yml at the repo root) runs the same stages as
separate one-shot services. This orchestrator exists for the ephemeral
GitHub Actions world, where the broker lives only as long as the job.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from pipeline import aggregator, dedupe, producers, scorer
from pipeline.common import make_transport


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--transport", default=os.environ.get("HORNINESS_TRANSPORT", "kafka"))
    ap.add_argument("--bootstrap", default=os.environ.get("KAFKA_BOOTSTRAP", "localhost:9092"))
    ap.add_argument("--topic-dir", default=os.environ.get("HORNINESS_TOPIC_DIR", ".horniness-topics"))
    ap.add_argument("--out", default="horniness-site")
    args = ap.parse_args()

    transport = make_transport(args.transport, args.bootstrap, args.topic_dir)
    try:
        if args.transport == "kafka":
            transport.wait_ready()
            transport.ensure_topics()
            print(f"broker ready at {args.bootstrap}, topics ensured")
        producers.run(transport)
        dedupe.run(transport)
        scorer.run(transport)
        aggregator.run(transport, args.out)
    finally:
        transport.close()
    print("pipeline complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
