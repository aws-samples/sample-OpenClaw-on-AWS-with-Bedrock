#!/usr/bin/env python3
"""
OpenClaw Multi-Tenant Platform — AWS Demo

Runs on an EC2 instance with OpenClaw + Bedrock already deployed.
Demonstrates the multi-tenant flow with REAL Bedrock model inference.

This script:
  1. Starts the Agent Container server (server.py) on port 8080
  2. Starts the Tenant Router on port 8090
  3. Sends test messages as different tenants through the full pipeline
  4. Shows real Bedrock responses with per-tenant permission enforcement

Prerequisites:
  - EC2 instance with OpenClaw deployed (standard CloudFormation stack)
  - Bedrock model access enabled
  - Python 3.10+ with boto3, requests

Run on EC2:
    sudo su - ubuntu
    cd /path/to/repo
    pip3 install requests boto3
    python3 demo/aws_demo.py

Or run the setup script first:
    bash demo/setup_aws_demo.sh
    python3 demo/aws_demo.py
"""

import json
import logging
import os
import re
import subprocess
import sys
import time
import signal
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "agent-container"))
sys.path.insert(0, os.path.join(REPO_ROOT, "auth-agent"))
sys.path.insert(0, os.path.join(REPO_ROOT, "src", "gateway"))

# Detect region from IMDS or env
def detect_region():
    """Detect AWS region from IMDS (EC2) or environment."""
    region = os.environ.get("AWS_REGION")
    if region:
        return region
    try:
        import requests as req
        token = req.put(
            "http://169.254.169.254/latest/api/token",
            headers={"X-aws-ec2-metadata-token-ttl-seconds": "21600"},
            timeout=2,
        ).text
        region = req.get(
            "http://169.254.169.254/latest/meta-data/placement/region",
            headers={"X-aws-ec2-metadata-token": token},
            timeout=2,
        ).text
        return region
    except Exception:
        return "us-east-1"

AWS_REGION = detect_region()
STACK_NAME = os.environ.get("STACK_NAME", "openclaw-multitenancy")
os.environ["AWS_REGION"] = AWS_REGION
os.environ["STACK_NAME"] = STACK_NAME

AGENT_CONTAINER_PORT = 8080
TENANT_ROUTER_PORT = 8090

# ---------------------------------------------------------------------------
# Colors
# ---------------------------------------------------------------------------
class C:
    HEADER = "\033[95m"
    BLUE = "\033[94m"
    CYAN = "\033[96m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    END = "\033[0m"

def banner(text):
    w = 70
    print(f"\n{C.BOLD}{C.HEADER}{'=' * w}")
    print(f"  {text}")
    print(f"{'=' * w}{C.END}\n")

def section(text):
    print(f"\n{C.BOLD}{C.CYAN}--- {text} ---{C.END}\n")

def ok(text):
    print(f"  {C.GREEN}✓{C.END} {text}")

def fail(text):
    print(f"  {C.RED}✗{C.END} {text}")

def info(text):
    print(f"  {C.DIM}{text}{C.END}")

def warn(text):
    print(f"  {C.YELLOW}⚠{C.END} {text}")


# ---------------------------------------------------------------------------
# Step 1: Setup SSM permission profiles for demo tenants
# ---------------------------------------------------------------------------

def setup_tenant_profiles():
    """Create demo tenant permission profiles in SSM Parameter Store."""
    import boto3
    ssm = boto3.client("ssm", region_name=AWS_REGION)

    profiles = {
        "wa__intern_sarah": {
            "profile": "basic",
            "tools": ["web_search"],
            "data_permissions": {"file_paths": [], "api_endpoints": []},
        },
        "tg__engineer_alex": {
            "profile": "advanced",
            "tools": ["web_search", "shell", "browser", "file", "file_write", "code_execution"],
            "data_permissions": {"file_paths": ["/home/ubuntu/projects/*"], "api_endpoints": []},
        },
        "dc__admin_jordan": {
            "profile": "advanced",
            "tools": ["web_search", "shell", "browser", "file", "file_write", "code_execution"],
            "data_permissions": {"file_paths": ["/*"], "api_endpoints": ["*"]},
        },
    }

    for tenant_id, profile in profiles.items():
        path = f"/openclaw/{STACK_NAME}/tenants/{tenant_id}/permissions"
        try:
            ssm.put_parameter(
                Name=path,
                Value=json.dumps(profile),
                Type="String",
                Overwrite=True,
            )
            ok(f"SSM profile created: {tenant_id} → {profile['profile']} (tools={profile['tools']})")
        except Exception as e:
            fail(f"SSM profile failed for {tenant_id}: {e}")
            return False
    return True


# ---------------------------------------------------------------------------
# Step 2: Start Agent Container server
# ---------------------------------------------------------------------------

_child_processes = []

def start_agent_container():
    """Start the Agent Container server.py on port 8080.

    This is the same server that runs inside AgentCore Runtime containers.
    Here we run it directly on EC2 for the demo.
    """
    env = os.environ.copy()
    env["PORT"] = str(AGENT_CONTAINER_PORT)
    env["STACK_NAME"] = STACK_NAME
    env["AWS_REGION"] = AWS_REGION

    # Use the Bedrock model from the existing OpenClaw config
    model_id = os.environ.get("BEDROCK_MODEL_ID", "")
    if not model_id:
        # Try to read from OpenClaw config
        config_path = os.path.expanduser("~/.openclaw/openclaw.json")
        if os.path.exists(config_path):
            try:
                with open(config_path) as f:
                    config = json.load(f)
                # Extract model ID from config
                providers = config.get("models", {}).get("providers", {})
                for provider in providers.values():
                    models = provider.get("models", [])
                    if models:
                        model_id = models[0].get("id", "")
                        break
            except Exception:
                pass
    if not model_id:
        model_id = "global.amazon.nova-2-lite-v1:0"

    env["BEDROCK_MODEL_ID"] = model_id
    info(f"Using Bedrock model: {model_id}")

    server_path = os.path.join(REPO_ROOT, "agent-container", "server.py")
    proc = subprocess.Popen(
        [sys.executable, server_path],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        cwd=os.path.join(REPO_ROOT, "agent-container"),
    )
    _child_processes.append(proc)
    return proc


def start_tenant_router():
    """Start the Tenant Router on port 8090."""
    env = os.environ.copy()
    env["ROUTER_PORT"] = str(TENANT_ROUTER_PORT)
    env["STACK_NAME"] = STACK_NAME
    env["AWS_REGION"] = AWS_REGION
    # Point to local Agent Container instead of AgentCore Runtime
    env["AGENT_CONTAINER_URL"] = f"http://localhost:{AGENT_CONTAINER_PORT}"

    router_path = os.path.join(REPO_ROOT, "src", "gateway", "tenant_router.py")
    proc = subprocess.Popen(
        [sys.executable, router_path],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    _child_processes.append(proc)
    return proc


def wait_for_service(port, name, timeout=60):
    """Wait for an HTTP service to become ready."""
    import requests as req
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = req.get(f"http://localhost:{port}/ping", timeout=2)
            if r.status_code == 200:
                ok(f"{name} ready on port {port}")
                return True
        except Exception:
            pass
        time.sleep(1)
    fail(f"{name} did not start within {timeout}s on port {port}")
    return False


# ---------------------------------------------------------------------------
# Step 3: Send test messages through the pipeline
# ---------------------------------------------------------------------------

def send_message(channel, user_id, message, persona):
    """Send a message through the Tenant Router → Agent Container pipeline."""
    import requests as req

    print(f"\n  {C.BOLD}[{persona}]{C.END} via {channel}: \"{message}\"")

    # Call Tenant Router
    try:
        resp = req.post(
            f"http://localhost:{TENANT_ROUTER_PORT}/route",
            json={
                "channel": channel,
                "user_id": user_id,
                "message": message,
            },
            timeout=120,  # Bedrock can take a while
        )

        if resp.status_code == 200:
            result = resp.json()
            tenant_id = result.get("tenant_id", "?")
            response = result.get("response", {})

            ok(f"Tenant: {tenant_id}")

            # Extract the actual text response
            if isinstance(response, dict):
                choices = response.get("choices", [])
                if choices:
                    content = choices[0].get("message", {}).get("content", "")
                    ok(f"Response: {content[:200]}{'...' if len(content) > 200 else ''}")
                else:
                    info(f"Raw response: {json.dumps(response)[:200]}")
            else:
                info(f"Response: {str(response)[:200]}")

            return result
        else:
            fail(f"HTTP {resp.status_code}: {resp.text[:200]}")
            return None

    except req.exceptions.Timeout:
        fail("Request timed out (120s)")
        return None
    except Exception as e:
        fail(f"Request failed: {e}")
        return None


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------

def cleanup():
    """Terminate all child processes."""
    for proc in _child_processes:
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
    info("All demo processes terminated")


def signal_handler(sig, frame):
    cleanup()
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)


# ===========================================================================
# MAIN
# ===========================================================================

def main():
    banner("OpenClaw Multi-Tenant Platform — AWS Demo")
    print(f"  {C.DIM}Region: {AWS_REGION}")
    print(f"  Stack: {STACK_NAME}")
    print(f"  This demo uses REAL Bedrock model inference.{C.END}")

    # ------------------------------------------------------------------
    # Phase 1: Setup tenant profiles in SSM
    # ------------------------------------------------------------------
    section("Phase 1: Create tenant permission profiles in SSM")
    if not setup_tenant_profiles():
        fail("Could not create SSM profiles. Check AWS credentials and permissions.")
        return

    # ------------------------------------------------------------------
    # Phase 2: Start services
    # ------------------------------------------------------------------
    section("Phase 2: Start Agent Container + Tenant Router")

    info("Starting Agent Container (server.py) on port 8080...")
    info("This will start an OpenClaw subprocess internally — may take 30s...")
    container_proc = start_agent_container()

    info("Starting Tenant Router on port 8090...")
    router_proc = start_tenant_router()

    # Wait for services
    info("Waiting for services to be ready...")

    # Tenant Router should be fast
    if not wait_for_service(TENANT_ROUTER_PORT, "Tenant Router", timeout=15):
        fail("Tenant Router failed to start. Check logs.")
        cleanup()
        return

    # Agent Container needs OpenClaw subprocess to start (slower)
    if not wait_for_service(AGENT_CONTAINER_PORT, "Agent Container", timeout=90):
        warn("Agent Container not ready via /ping. It may still be starting OpenClaw subprocess.")
        warn("Continuing anyway — first request may take longer...")

    # ------------------------------------------------------------------
    # Phase 3: Send test messages
    # ------------------------------------------------------------------
    section("Phase 3: Multi-tenant message processing")

    print(f"\n  {C.BOLD}Scenario 1: Intern (basic profile — web_search only){C.END}")
    result1 = send_message(
        channel="whatsapp",
        user_id="intern_sarah",
        message="What is Amazon Bedrock? Give me a one-sentence answer.",
        persona="Intern (Sarah)",
    )

    print(f"\n  {C.BOLD}Scenario 2: Engineer (advanced profile — shell allowed){C.END}")
    result2 = send_message(
        channel="telegram",
        user_id="engineer_alex",
        message="What is the capital of France? One word answer.",
        persona="Engineer (Alex)",
    )

    print(f"\n  {C.BOLD}Scenario 3: Admin (advanced profile — install_skill always blocked){C.END}")
    result3 = send_message(
        channel="discord",
        user_id="admin_jordan",
        message="Can you install a new skill for me? Just say yes or no.",
        persona="Admin (Jordan)",
    )

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    banner("Demo Complete")

    results = [
        ("Intern (Sarah)", "wa__intern_sarah", result1),
        ("Engineer (Alex)", "tg__engineer_alex", result2),
        ("Admin (Jordan)", "dc__admin_jordan", result3),
    ]

    print(f"  {C.BOLD}Results:{C.END}\n")
    for persona, tenant_id, result in results:
        status = f"{C.GREEN}✓{C.END}" if result else f"{C.RED}✗{C.END}"
        print(f"  {status} {persona} (tenant={tenant_id})")

    print(f"\n  {C.BOLD}What happened:{C.END}")
    print(f"  1. Three users sent messages via three different channels")
    print(f"  2. Tenant Router derived unique tenant_id for each")
    print(f"  3. Agent Container loaded per-tenant permission profiles from SSM")
    print(f"  4. System prompt was customized per tenant (Plan A)")
    print(f"  5. Bedrock processed each request with tenant-specific constraints")
    print(f"  6. Responses were audited for policy violations (Plan E)")
    print(f"\n  {C.BOLD}In production:{C.END}")
    print(f"  Each tenant would run in an isolated Firecracker microVM via AgentCore Runtime.")
    print(f"  This demo runs all tenants on the same EC2 instance for simplicity.")

    print(f"\n  {C.DIM}Cleaning up...{C.END}")
    cleanup()
    print()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n{C.YELLOW}Interrupted{C.END}")
        cleanup()
    except Exception as e:
        print(f"\n{C.RED}Error: {e}{C.END}")
        cleanup()
        raise
