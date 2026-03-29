import os
import time
import subprocess
from datetime import datetime

# ── AUTO-SCALING POLICY CONFIGURATION ──────────────────────
THRESHOLD       = 70    # CPU % to trigger scale-out
CHECK_INTERVAL  = 5     # Seconds between checks
COOLDOWN        = 300   # Seconds before re-triggering (5 min)
MAX_INSTANCES   = 3     # Maximum cloud instances allowed
GCP_PROJECT     = "auto-scale-vm-project"
GCP_ZONE        = "asia-south2-a"
MACHINE_TYPE    = "e2-micro"
# ────────────────────────────────────────────────────────────

triggered_count = 0
last_trigger    = 0

def log(msg):
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f"[{ts}] {msg}")

def get_cpu_usage():
    output = os.popen("top -bn1 | grep 'Cpu(s)'").read()
    try:
        parts = output.split()
        for i, p in enumerate(parts):
            if 'id' in p and i > 0:
                idle = float(parts[i-1].replace(',', '.'))
                return round(100 - idle, 1)
        idle = float(output.split()[7])
        return round(100 - idle, 1)
    except:
        return 0.0

def get_ram_usage():
    try:
        info = {}
        with open('/proc/meminfo') as f:
            for line in f:
                p = line.split()
                info[p[0].rstrip(':')] = int(p[1])
        used = info['MemTotal'] - info['MemAvailable']
        return round(used * 100 / info['MemTotal'], 1)
    except:
        return 0.0

def count_running_instances():
    try:
        result = subprocess.run([
            'gcloud', 'compute', 'instances', 'list',
            '--filter=name:autoscale-vm',
            '--format=value(name)',
            f'--project={GCP_PROJECT}'
        ], capture_output=True, text=True)
        instances = [l for l in result.stdout.strip().split('\n') if l]
        return len(instances)
    except:
        return 0

def create_startup_script():
    script = '''#!/bin/bash
apt-get update -y
apt-get install -y python3 python3-pip
pip3 install flask
mkdir -p /opt/app
cat > /opt/app/app.py << 'PYEOF'
from flask import Flask, jsonify
import socket, datetime
app = Flask(__name__)

@app.route("/")
def home():
    host = socket.gethostname()
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return f"""
<html>
<head>
  <title>Auto-Scaled App</title>
  <meta http-equiv="refresh" content="5">
  <style>
    body{{font-family:Arial,sans-serif;background:#f0f7ff;padding:40px;margin:0}}
    .card{{background:white;border-radius:14px;padding:28px;
           max-width:520px;border:1px solid #e0e0e0}}
    .badge{{background:#e8f5e9;color:#2e7d32;padding:5px 14px;
            border-radius:20px;font-size:13px;font-weight:500;
            display:inline-block;margin-bottom:18px}}
    h1{{color:#1a73e8;font-size:22px;margin:0 0 6px}}
    p.sub{{color:#666;font-size:13px;margin:0 0 20px}}
    table{{width:100%;border-collapse:collapse}}
    td{{padding:10px 8px;border-bottom:1px solid #f5f5f5;font-size:14px}}
    td:first-child{{color:#777;width:45%}}
    td:last-child{{font-weight:500}}
    .green{{color:#2e7d32}}
    .note{{color:#999;font-size:12px;margin-top:14px}}
  </style>
</head>
<body>
<div class="card">
  <div class="badge">Running on Google Cloud Platform</div>
  <h1>Auto-Scaled Application</h1>
  <p class="sub">Launched automatically when local CPU exceeded 70%</p>
  <table>
    <tr><td>Hostname</td><td>{host}</td></tr>
    <tr><td>Server Time</td><td>{now}</td></tr>
    <tr><td>Machine Type</td><td>e2-micro (GCP)</td></tr>
    <tr><td>Zone</td><td>asia-south2-a</td></tr>
    <tr><td>Project</td><td>auto-scale-vm-project</td></tr>
    <tr><td>Status</td><td class="green">Online</td></tr>
  </table>
  <p class="note">Page auto-refreshes every 5 seconds</p>
</div>
</body>
</html>"""

@app.route("/health")
def health():
    return jsonify({{
        "status": "healthy",
        "host": socket.gethostname(),
        "cloud": "GCP",
        "time": str(datetime.datetime.now())
    }}), 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=80)
PYEOF
python3 /opt/app/app.py &
echo "App started at $(date)" >> /var/log/app_startup.log
'''
    with open('/tmp/startup.sh', 'w') as f:
        f.write(script)
    return '/tmp/startup.sh'

def deploy_to_gcp(cpu, ram):
    instance_name = f"autoscale-vm-{int(time.time())}"
    log(f"Creating GCP instance: {instance_name}")
    startup_file = create_startup_script()
    cmd = (
        f"gcloud compute instances create {instance_name} "
        f"--zone={GCP_ZONE} "
        f"--machine-type={MACHINE_TYPE} "
        f"--image-family=ubuntu-2204-lts "
        f"--image-project=ubuntu-os-cloud "
        f"--tags=http-server "
        f"--metadata-from-file=startup-script={startup_file} "
        f"--metadata=cpu-trigger={cpu},ram-trigger={ram} "
        f"--project={GCP_PROJECT}"
    )
    result = os.system(cmd)
    return instance_name if result == 0 else None

# ── MAIN MONITORING LOOP ─────────────────────────────────────
log("=" * 55)
log("  Resource Monitor + Auto-Scaling Policy Active")
log(f"  Threshold     : {THRESHOLD}%")
log(f"  Check every   : {CHECK_INTERVAL}s")
log(f"  Cooldown      : {COOLDOWN}s")
log(f"  Max instances : {MAX_INSTANCES}")
log(f"  Target cloud  : GCP {GCP_ZONE}")
log("=" * 55)

while True:
    cpu = get_cpu_usage()
    ram = get_ram_usage()
    now = time.time()

    log(f"CPU: {cpu}%  |  RAM: {ram}%  |  Threshold: {THRESHOLD}%")

    if cpu > THRESHOLD:
        elapsed = now - last_trigger
        current = count_running_instances()

        if current >= MAX_INSTANCES:
            log(f"Max instances ({MAX_INSTANCES}) reached. Skipping.")
        elif elapsed < COOLDOWN and last_trigger > 0:
            log(f"In cooldown. {int(COOLDOWN-elapsed)}s remaining.")
        else:
            log("=" * 55)
            log(f"ALERT: CPU={cpu}% exceeds {THRESHOLD}% threshold!")
            log("AUTO-SCALING POLICY: Triggering cloud deployment...")
            log("=" * 55)
            name = deploy_to_gcp(cpu, ram)
            if name:
                triggered_count += 1
                last_trigger = now
                log(f"SUCCESS: Instance {name} launched!")
                log(f"Scale-out count: {triggered_count}")
                break
            else:
                log("ERROR: Check gcloud config and project ID.")
    else:
        log("CPU normal. No scaling needed.")

    time.sleep(CHECK_INTERVAL)
