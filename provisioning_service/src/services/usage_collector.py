"""
Usage Collector Service

Background service that collects token usage data from OpenClaw instances
and aggregates it into hourly and daily summaries.

Flow:
1. Every 5 minutes, scan all openclaw-* namespaces
2. For each namespace, exec into the pod and read session JSONL files
3. Parse usage data and insert into usage_events table
4. Aggregate into hourly_usage and daily_usage tables
5. Clean up old raw events (7-day retention)
"""

import logging
import time
import threading
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from kubernetes import client
from kubernetes.stream import stream
import json

from app.database import get_db_connection, insert_usage_event, cleanup_old_usage_events

logger = logging.getLogger(__name__)

# Model pricing (per million tokens)
MODEL_PRICING = {
    # Bedrock Claude models
    'claude-opus-4-6': {'input': 15.0, 'output': 75.0},
    'claude-opus-4-5': {'input': 15.0, 'output': 75.0},
    'claude-sonnet-4-6': {'input': 3.0, 'output': 15.0},
    'claude-sonnet-4-5': {'input': 3.0, 'output': 15.0},
    'claude-haiku-4-5': {'input': 0.8, 'output': 4.0},

    # SiliconFlow models (approximate pricing)
    'deepseek-v3': {'input': 0.27, 'output': 1.10},
    'qwen-2.5': {'input': 0.14, 'output': 0.6},

    # Default fallback
    'unknown': {'input': 3.0, 'output': 15.0}
}

def calculate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """
    Calculate estimated cost for token usage

    Args:
        model: Model name (e.g., 'claude-opus-4-6', 'deepseek-v3')
        input_tokens: Number of input tokens
        output_tokens: Number of output tokens

    Returns:
        Estimated cost in USD
    """
    # Extract base model name (handle variations like 'bedrock/us.anthropic.claude-opus-4-6-v1:0')
    model_lower = model.lower()

    pricing = None
    for key in MODEL_PRICING.keys():
        if key in model_lower:
            pricing = MODEL_PRICING[key]
            break

    if not pricing:
        pricing = MODEL_PRICING['unknown']
        logger.warning(f"⚠️ Unknown model pricing for '{model}', using default")

    input_cost = (input_tokens / 1_000_000) * pricing['input']
    output_cost = (output_tokens / 1_000_000) * pricing['output']

    return round(input_cost + output_cost, 6)


class UsageCollector:
    """Background service to collect usage data from OpenClaw instances"""

    def __init__(self, interval: int = 300):
        """
        Initialize usage collector

        Args:
            interval: Collection interval in seconds (default: 300 = 5 minutes)
        """
        self.interval = interval
        self.k8s_core = client.CoreV1Api()
        self.running = False
        self._last_collection_time = None

    def collect_from_pod(self, namespace: str, pod_name: str, user_id: str, user_email: str) -> List[Dict]:
        """
        Collect usage data from a single OpenClaw pod

        Args:
            namespace: Kubernetes namespace
            pod_name: Pod name
            user_id: User ID (hashed)
            user_email: User email

        Returns:
            List of usage events
        """
        events = []

        try:
            # Find session files modified in last 10 minutes
            find_command = [
                'sh', '-c',
                'find /home/openclaw/.openclaw/sessions -name "*.jsonl" -mmin -10 2>/dev/null || true'
            ]

            exec_resp = stream(
                self.k8s_core.connect_get_namespaced_pod_exec,
                pod_name,
                namespace,
                container='openclaw',
                command=find_command,
                stderr=True,
                stdin=False,
                stdout=True,
                tty=False
            )

            session_files = [f.strip() for f in exec_resp.split('\n') if f.strip()]

            if not session_files:
                logger.debug(f"No recent session files in {namespace}/{pod_name}")
                return events

            logger.debug(f"Found {len(session_files)} session files in {namespace}/{pod_name}")

            # Read each session file
            for session_file in session_files:
                try:
                    # Read file content
                    cat_command = ['cat', session_file]
                    file_content = stream(
                        self.k8s_core.connect_get_namespaced_pod_exec,
                        pod_name,
                        namespace,
                        container='openclaw',
                        command=cat_command,
                        stderr=False,
                        stdin=False,
                        stdout=True,
                        tty=False
                    )

                    # Parse JSONL
                    for line in file_content.split('\n'):
                        if not line.strip():
                            continue

                        try:
                            entry = json.loads(line)

                            # Extract usage data
                            usage = entry.get('usage', {})
                            if not usage:
                                continue

                            # Extract model and provider
                            model = entry.get('model', 'unknown')
                            provider = 'bedrock' if 'bedrock' in model.lower() else 'siliconflow'

                            # Extract token counts
                            input_tokens = usage.get('inputTokens', 0)
                            output_tokens = usage.get('outputTokens', 0)
                            cache_read = usage.get('cacheReadTokens', 0)
                            cache_write = usage.get('cacheCreationTokens', 0)
                            total_tokens = input_tokens + output_tokens + cache_read + cache_write

                            if total_tokens == 0:
                                continue

                            # Calculate cost
                            estimated_cost = calculate_cost(model, input_tokens, output_tokens)

                            # Create event
                            event = {
                                'user_id': user_id,
                                'user_email': user_email,
                                'model': model,
                                'provider': provider,
                                'input_tokens': input_tokens,
                                'output_tokens': output_tokens,
                                'cache_read': cache_read,
                                'cache_write': cache_write,
                                'total_tokens': total_tokens,
                                'timestamp': int(datetime.utcnow().timestamp() * 1000)
                            }

                            events.append(event)

                        except json.JSONDecodeError:
                            continue

                except Exception as e:
                    logger.warning(f"Failed to read session file {session_file}: {e}")
                    continue

        except Exception as e:
            logger.error(f"Failed to collect usage from {namespace}/{pod_name}: {e}")

        return events

    def collect_all_instances(self) -> int:
        """
        Collect usage from all OpenClaw instances

        Returns:
            Number of events collected
        """
        total_events = 0

        try:
            # List all namespaces starting with 'openclaw-'
            namespaces = self.k8s_core.list_namespace()
            openclaw_namespaces = [
                ns.metadata.name for ns in namespaces.items
                if ns.metadata.name.startswith('openclaw-') and ns.metadata.name != 'openclaw-operator-system'
            ]

            logger.info(f"Found {len(openclaw_namespaces)} OpenClaw instance namespaces")

            for namespace in openclaw_namespaces:
                try:
                    # Extract user_id from namespace (openclaw-<user_id>)
                    user_id = namespace.replace('openclaw-', '')

                    # List pods in namespace
                    pods = self.k8s_core.list_namespaced_pod(namespace)
                    openclaw_pods = [
                        pod for pod in pods.items
                        if pod.metadata.name.startswith('openclaw-') and pod.status.phase == 'Running'
                    ]

                    if not openclaw_pods:
                        logger.debug(f"No running pods in {namespace}")
                        continue

                    # Get user email from pod labels or annotations
                    pod = openclaw_pods[0]
                    user_email = pod.metadata.labels.get('user-email', f"{user_id}@unknown")

                    # Collect usage from pod
                    events = self.collect_from_pod(namespace, pod.metadata.name, user_id, user_email)

                    # Insert events into database
                    for event in events:
                        try:
                            insert_usage_event(event)
                            total_events += 1
                        except Exception as e:
                            logger.error(f"Failed to insert usage event: {e}")

                    if events:
                        logger.info(f"Collected {len(events)} usage events from {namespace}")

                except Exception as e:
                    logger.error(f"Failed to process namespace {namespace}: {e}")
                    continue

        except Exception as e:
            logger.error(f"Failed to list namespaces: {e}")

        return total_events

    def aggregate_hourly(self):
        """Aggregate usage_events into hourly_usage table"""
        try:
            conn = get_db_connection()
            cursor = conn.cursor()

            # Aggregate events from the last 2 hours
            cursor.execute('''
                INSERT OR REPLACE INTO hourly_usage (
                    user_id, user_email, model, provider, hour,
                    input_tokens, output_tokens, cache_read, cache_write, total_tokens,
                    call_count, estimated_cost, updated_at
                )
                SELECT
                    user_id,
                    user_email,
                    model,
                    provider,
                    datetime(timestamp / 1000, 'unixepoch', 'start of hour') as hour,
                    SUM(input_tokens) as input_tokens,
                    SUM(output_tokens) as output_tokens,
                    SUM(cache_read) as cache_read,
                    SUM(cache_write) as cache_write,
                    SUM(total_tokens) as total_tokens,
                    COUNT(*) as call_count,
                    SUM(
                        CASE
                            WHEN model LIKE '%opus%' THEN (input_tokens / 1000000.0 * 15.0 + output_tokens / 1000000.0 * 75.0)
                            WHEN model LIKE '%sonnet%' THEN (input_tokens / 1000000.0 * 3.0 + output_tokens / 1000000.0 * 15.0)
                            WHEN model LIKE '%haiku%' THEN (input_tokens / 1000000.0 * 0.8 + output_tokens / 1000000.0 * 4.0)
                            WHEN model LIKE '%deepseek%' THEN (input_tokens / 1000000.0 * 0.27 + output_tokens / 1000000.0 * 1.10)
                            ELSE (input_tokens / 1000000.0 * 3.0 + output_tokens / 1000000.0 * 15.0)
                        END
                    ) as estimated_cost,
                    CURRENT_TIMESTAMP as updated_at
                FROM usage_events
                WHERE timestamp >= (strftime('%s', 'now', '-2 hours') * 1000)
                GROUP BY user_id, user_email, model, provider, hour
            ''')

            affected = cursor.rowcount
            conn.commit()
            conn.close()

            if affected > 0:
                logger.info(f"✅ Aggregated {affected} records into hourly_usage")

        except Exception as e:
            logger.error(f"Failed to aggregate hourly usage: {e}")

    def aggregate_daily(self):
        """Aggregate hourly_usage into daily_usage table"""
        try:
            conn = get_db_connection()
            cursor = conn.cursor()

            # Aggregate hourly data into daily (last 2 days)
            cursor.execute('''
                INSERT OR REPLACE INTO daily_usage (
                    user_id, user_email, model, provider, date,
                    input_tokens, output_tokens, cache_read, cache_write, total_tokens,
                    call_count, estimated_cost, updated_at
                )
                SELECT
                    user_id,
                    user_email,
                    model,
                    provider,
                    date(hour) as date,
                    SUM(input_tokens) as input_tokens,
                    SUM(output_tokens) as output_tokens,
                    SUM(cache_read) as cache_read,
                    SUM(cache_write) as cache_write,
                    SUM(total_tokens) as total_tokens,
                    SUM(call_count) as call_count,
                    SUM(estimated_cost) as estimated_cost,
                    CURRENT_TIMESTAMP as updated_at
                FROM hourly_usage
                WHERE date(hour) >= date('now', '-2 days')
                GROUP BY user_id, user_email, model, provider, date
            ''')

            affected = cursor.rowcount
            conn.commit()
            conn.close()

            if affected > 0:
                logger.info(f"✅ Aggregated {affected} records into daily_usage")

        except Exception as e:
            logger.error(f"Failed to aggregate daily usage: {e}")

    def cleanup_old_events(self):
        """Clean up raw events older than 7 days"""
        try:
            cleanup_old_usage_events(days=7)
        except Exception as e:
            logger.error(f"Failed to cleanup old events: {e}")

    def run_collection_cycle(self):
        """Run one collection cycle"""
        logger.info("🔄 Starting usage collection cycle")
        start_time = time.time()

        try:
            # Collect from all instances
            event_count = self.collect_all_instances()

            # Aggregate data
            self.aggregate_hourly()
            self.aggregate_daily()

            # Cleanup old events (run once per hour)
            if (not self._last_collection_time or
                    (datetime.now() - self._last_collection_time).seconds >= 3600):
                self.cleanup_old_events()
                self._last_collection_time = datetime.now()

            elapsed = time.time() - start_time
            logger.info(f"✅ Collection cycle completed: {event_count} events in {elapsed:.2f}s")

        except Exception as e:
            logger.error(f"❌ Collection cycle failed: {e}")
            import traceback
            traceback.print_exc()

    def run(self):
        """Run the usage collector in a loop"""
        self.running = True
        logger.info(f"🚀 Usage collector started (interval: {self.interval}s)")

        while self.running:
            try:
                self.run_collection_cycle()

                # Sleep until next cycle
                time.sleep(self.interval)

            except Exception as e:
                logger.error(f"❌ Usage collector error: {e}")
                time.sleep(60)  # Sleep 1 minute on error

    def stop(self):
        """Stop the usage collector"""
        self.running = False
        logger.info("⏹️ Usage collector stopped")
