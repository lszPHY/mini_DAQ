## Python dependencies

python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

## System dependencies (RHEL 9)

```bash
sudo dnf install -y \
  python3.13-devel \
  libpcap-devel \
  gcc-c++ make

