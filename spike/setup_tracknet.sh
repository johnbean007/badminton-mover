#!/usr/bin/env bash
# Fetch TrackNetV3 (MIT licence) and its trained checkpoints into vendor/, then apply two small patches:
#   TRACKNET_WORKERS  data-loader worker count (default 0; 16 workers freeze on macOS)
#   TRACKNET_DEVICE   force a PyTorch device, e.g. cpu, to time CPU-only runs
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d vendor/TrackNetV3 ]; then
  git clone --depth 1 https://github.com/qaz812345/TrackNetV3.git vendor/TrackNetV3
fi
cd vendor/TrackNetV3

if [ ! -f ckpts/TrackNet_best.pt ]; then
  uv run --project ../.. gdown 1CfzE87a0f6LhBp0kniSl1-89zaLCZ8cA -O TrackNetV3_ckpts.zip
  unzip -o -q TrackNetV3_ckpts.zip
  rm TrackNetV3_ckpts.zip
fi

if ! grep -q TRACKNET_WORKERS predict.py; then
  sed -i '' 's/    num_workers = args.batch_size if args.batch_size <= 16 else 16/    num_workers = int(os.environ.get("TRACKNET_WORKERS", "0"))/' predict.py
fi
if ! grep -q TRACKNET_DEVICE predict.py; then
  sed -i '' 's/^if torch.cuda.is_available():/if os.environ.get("TRACKNET_DEVICE"):\n    device = torch.device(os.environ["TRACKNET_DEVICE"])\nelif torch.cuda.is_available():/' predict.py
fi
echo "TrackNetV3 ready in $(pwd)"
