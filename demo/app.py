import os
import random
import logging
import secrets
import hashlib
from flask import Flask, request, jsonify
from prometheus_client import Counter, generate_latest, CONTENT_TYPE_LATEST
try:
    from flagsmith import Flagsmith
    from flagsmith.analytics import EventProcessorConfig
except (ImportError, AttributeError):
    Flagsmith = None
    EventProcessorConfig = None

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("demo_checkout")

app = Flask(__name__)

FLAGSMITH_ENV_KEY = os.environ.get("FLAGSMITH_ENVIRONMENT_KEY", "")
FLAGSMITH_API_URL = os.environ.get("FLAGSMITH_API_URL", "http://flagsmith:8000/api/v1/")
FLAGSMITH_EVENTS_API_URL = os.environ.get(
    "FLAGSMITH_EVENTS_API_URL",
    FLAGSMITH_API_URL.replace("/v1/", "/").replace("/v1", "/")
)
BUG_IN_NEW_CHECKOUT = os.environ.get("BUG_IN_NEW_CHECKOUT", "1") == "1"
SIMULATE_SRM = os.environ.get("SIMULATE_SRM", "0").lower() in ("1", "true", "yes")
EXPERIMENT_MODE = os.environ.get("EXPERIMENT_MODE", "balanced")  # "winner", "worse", "balanced"
DEMO_API_TOKEN = os.environ.get("DEMO_API_TOKEN", "")

def check_demo_auth() -> bool:
    """Validate API token if DEMO_API_TOKEN is configured."""
    if not DEMO_API_TOKEN:
        return True
    auth = request.headers.get("Authorization", "")
    token = None
    if auth.startswith("Bearer "):
        token = auth.split(" ", 1)[1].strip()
    elif request.headers.get("X-API-Token"):
        token = request.headers.get("X-API-Token")
    if token and secrets.compare_digest(token, DEMO_API_TOKEN):
        return True
    return False

logger.info(f"Initializing Flagsmith client with API {FLAGSMITH_API_URL} and Events {FLAGSMITH_EVENTS_API_URL}")
flagsmith_client = Flagsmith(
    environment_key=FLAGSMITH_ENV_KEY,
    api_url=FLAGSMITH_API_URL,
    request_timeout_seconds=10,
    enable_events=True,
    event_processor_config=EventProcessorConfig(
        events_api_url=FLAGSMITH_EVENTS_API_URL,
        flush_interval_seconds=0.5,
        max_buffer_items=100,
    ),
)

# Prometheus Metrics
CHECKOUT_REQUESTS_TOTAL = Counter(
    "checkout_requests_total",
    "Total checkout requests processed",
    ["variant", "status"]
)

CHECKOUT_ERRORS_TOTAL = Counter(
    "checkout_errors_total",
    "Total checkout errors encountered",
    ["variant"]
)

# Initialize zero metrics for both variants so series always exist in Prometheus
for v in ("old_flow", "new_flow"):
    CHECKOUT_ERRORS_TOTAL.labels(variant=v)
    for s in ("200", "500"):
        CHECKOUT_REQUESTS_TOTAL.labels(variant=v, status=s)

@app.route("/checkout", methods=["POST", "GET"])
def checkout():
    data = request.get_json(silent=True) or {}
    user_id = request.args.get("user_id") or data.get("user_id") or f"user_{random.randint(1, 500)}"
    force_srm = request.args.get("srm") == "1" or SIMULATE_SRM

    # 1. Request the experiment flag (checkout_experiment) for the identified user
    exp_variant = "control"
    try:
        if force_srm:
            # Severe Sample Ratio Mismatch (SRM) simulation:
            # Drop variation exposures entirely to simulate broken tracking in new variant
            identity_flags = flagsmith_client.get_identity_flags(identifier=user_id)
            exp_flag = identity_flags.get_flag("checkout_experiment")
            exp_variant = exp_flag.value if exp_flag else "control"
            should_track = (exp_variant != "variation")
            if should_track:
                flagsmith_client.track_exposure_event(
                    feature_name="checkout_experiment",
                    identifier=user_id,
                    value=exp_variant,
                )
        else:
            exp_flag = flagsmith_client.get_experiment_flag("checkout_experiment", identifier=user_id)
            if exp_flag and (getattr(exp_flag, "variant", None) or exp_flag.value):
                exp_variant = getattr(exp_flag, "variant", None) or exp_flag.value
                if getattr(exp_flag, "variant", None) is None and exp_flag.value:
                    exp_metadata = getattr(exp_flag, "experiment", None)
                    exp_id = getattr(exp_metadata, "id", None) if exp_metadata else None
                    flagsmith_client.track_exposure_event(
                        feature_name="checkout_experiment",
                        identifier=user_id,
                        value=exp_flag.value,
                        metadata={"experiment_id": exp_id} if exp_id else None,
                    )
    except Exception as e:
        logger.warning(f"Experiment flag evaluation failed for {user_id}: {e}")

    # 2. Evaluate operational rollout flag (new_checkout)
    try:
        identity_flags = flagsmith_client.get_identity_flags(identifier=user_id)
        is_new_flow = identity_flags.is_feature_enabled("new_checkout")
    except Exception as e:
        logger.warning(f"Flagsmith flag evaluation failed for {user_id}, fallback to old_flow: {e}")
        is_new_flow = False

    variant = "new_flow" if is_new_flow else "old_flow"

    # Simulated fault injection: new flow fails ~50% when BUG_IN_NEW_CHECKOUT=1
    if is_new_flow and BUG_IN_NEW_CHECKOUT and random.random() < 0.50:
        CHECKOUT_REQUESTS_TOTAL.labels(variant=variant, status="500").inc()
        CHECKOUT_ERRORS_TOTAL.labels(variant=variant).inc()
        try:
            flagsmith_client.flush_events()
        except Exception:
            pass
        return jsonify({
            "status": "error",
            "variant": variant,
            "experiment_variant": exp_variant,
            "user_id": user_id,
            "error": "Payment processing error in new checkout flow",
        }), 500

    # 3. Normal successful checkout: track 'checkout_completed' conversion event
    # Dynamically adjusted based on EXPERIMENT_MODE:
    # - "worse": control ~60%, variation ~35% (chance_to_be_worse > 90% > 10% limit)
    # - "winner": control ~35%, variation ~60% (chance_to_win >= 95%, ci_low > 0)
    # - "balanced": control ~35%, variation ~35%
    # Uses deterministic hash per user_id to enforce true per-identity conversion in occurrence mode
    user_hash = int(hashlib.md5(f"{user_id}:conv".encode()).hexdigest(), 16)
    user_rand = (user_hash % 10000) / 10000.0

    if EXPERIMENT_MODE == "worse":
        if exp_variant == "control":
            should_convert = (user_rand < 0.60)
        else:
            should_convert = (user_rand < 0.35)
    elif EXPERIMENT_MODE == "winner":
        if exp_variant == "control":
            should_convert = (user_rand < 0.35)
        else:
            should_convert = (user_rand < 0.60)
    else:
        if exp_variant == "control":
            should_convert = (user_rand < 0.35)
        else:
            should_convert = (user_rand < 0.35)

    if should_convert:
        try:
            flagsmith_client.track_event(
                event="checkout_completed",
                identifier=user_id,
            )
            flagsmith_client.flush_events()
        except Exception as e:
            logger.warning(f"Track event checkout_completed failed for {user_id}: {e}")

    CHECKOUT_REQUESTS_TOTAL.labels(variant=variant, status="200").inc()
    return jsonify({
        "status": "success",
        "variant": variant,
        "experiment_variant": exp_variant,
        "user_id": user_id,
        "message": f"Checkout succeeded via {variant}",
    }), 200

@app.route("/metrics")
def metrics():
    return generate_latest(), 200, {"Content-Type": CONTENT_TYPE_LATEST}

@app.route("/toggle-bug", methods=["POST", "GET"])
def toggle_bug():
    if not check_demo_auth():
        return jsonify({"error": "Unauthorized: Invalid or missing API token"}), 401
    global BUG_IN_NEW_CHECKOUT
    val = request.args.get("enabled")
    if val is not None:
        BUG_IN_NEW_CHECKOUT = val.lower() in ("1", "true", "yes")
    else:
        BUG_IN_NEW_CHECKOUT = not BUG_IN_NEW_CHECKOUT
    logger.info(f"BUG_IN_NEW_CHECKOUT toggled to {BUG_IN_NEW_CHECKOUT}")
    return jsonify({"bug_enabled": BUG_IN_NEW_CHECKOUT}), 200

@app.route("/toggle-srm", methods=["POST", "GET"])
def toggle_srm():
    if not check_demo_auth():
        return jsonify({"error": "Unauthorized: Invalid or missing API token"}), 401
    global SIMULATE_SRM
    val = request.args.get("enabled")
    if val is not None:
        SIMULATE_SRM = val.lower() in ("1", "true", "yes")
    else:
        SIMULATE_SRM = not SIMULATE_SRM
    logger.info(f"SIMULATE_SRM toggled to {SIMULATE_SRM}")
    return jsonify({"simulate_srm": SIMULATE_SRM}), 200

@app.route("/reset-metrics", methods=["POST", "GET"])
def reset_metrics():
    if not check_demo_auth():
        return jsonify({"error": "Unauthorized: Invalid or missing API token"}), 401
    CHECKOUT_REQUESTS_TOTAL.clear()
    CHECKOUT_ERRORS_TOTAL.clear()
    for v in ("old_flow", "new_flow"):
        CHECKOUT_ERRORS_TOTAL.labels(variant=v)
        for s in ("200", "500"):
            CHECKOUT_REQUESTS_TOTAL.labels(variant=v, status=s)
    logger.info("Prometheus counter metrics cleared and zero-initialized")
    return jsonify({"status": "metrics_cleared"}), 200

@app.route("/set-experiment-mode", methods=["POST", "GET"])
def set_experiment_mode():
    if not check_demo_auth():
        return jsonify({"error": "Unauthorized: Invalid or missing API token"}), 401
    global EXPERIMENT_MODE
    val = request.args.get("mode") or ((request.get_json(silent=True) or {}).get("mode"))
    if val in ("winner", "worse", "balanced"):
        EXPERIMENT_MODE = val
        logger.info(f"EXPERIMENT_MODE changed to: {EXPERIMENT_MODE}")
        return jsonify({"status": "ok", "experiment_mode": EXPERIMENT_MODE}), 200
    return jsonify({"error": f"Invalid mode '{val}'. Choose 'winner', 'worse', or 'balanced'"}), 400

@app.route("/health")
def health():
    return jsonify({
        "status": "healthy",
        "bug_enabled": BUG_IN_NEW_CHECKOUT,
        "simulate_srm": SIMULATE_SRM,
        "experiment_mode": EXPERIMENT_MODE,
        "flagsmith_api": FLAGSMITH_API_URL,
    }), 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
