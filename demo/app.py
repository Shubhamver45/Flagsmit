import os
import random
import logging
from flask import Flask, request, jsonify
from prometheus_client import Counter, generate_latest, CONTENT_TYPE_LATEST
from flagsmith import Flagsmith

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("demo_checkout")

app = Flask(__name__)

FLAGSMITH_ENV_KEY = os.environ.get("FLAGSMITH_ENVIRONMENT_KEY", "N6LnMxtye9LM89yM7qwv8t")
FLAGSMITH_API_URL = os.environ.get("FLAGSMITH_API_URL", "http://flagsmith:8000/api/v1/")
BUG_IN_NEW_CHECKOUT = os.environ.get("BUG_IN_NEW_CHECKOUT", "1") == "1"

logger.info(f"Initializing Flagsmith client with API {FLAGSMITH_API_URL}")
flagsmith_client = Flagsmith(
    environment_key=FLAGSMITH_ENV_KEY,
    api_url=FLAGSMITH_API_URL,
    request_timeout_seconds=3,
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
    # Support GET or POST, with JSON body or query param
    data = request.get_json(silent=True) or {}
    user_id = request.args.get("user_id") or data.get("user_id") or f"user_{random.randint(1, 500)}"

    # Evaluate feature flag for this identity
    try:
        identity_flags = flagsmith_client.get_identity_flags(identifier=user_id)
        is_new_flow = identity_flags.is_feature_enabled("new_checkout")
    except Exception as e:
        logger.warning(f"Flagsmith flag evaluation failed for {user_id}, fallback to old_flow: {e}")
        is_new_flow = False

    variant = "new_flow" if is_new_flow else "old_flow"

    # Simulated fault injection: new flow fails ~30% when BUG_IN_NEW_CHECKOUT=1
    if is_new_flow and BUG_IN_NEW_CHECKOUT and random.random() < 0.30:
        CHECKOUT_REQUESTS_TOTAL.labels(variant=variant, status="500").inc()
        CHECKOUT_ERRORS_TOTAL.labels(variant=variant).inc()
        return jsonify({
            "status": "error",
            "variant": variant,
            "user_id": user_id,
            "error": "Payment processing error in new checkout flow",
        }), 500

    # Normal successful checkout
    CHECKOUT_REQUESTS_TOTAL.labels(variant=variant, status="200").inc()
    return jsonify({
        "status": "success",
        "variant": variant,
        "user_id": user_id,
        "message": f"Checkout succeeded via {variant}",
    }), 200

@app.route("/metrics")
def metrics():
    return generate_latest(), 200, {"Content-Type": CONTENT_TYPE_LATEST}

@app.route("/toggle-bug", methods=["POST", "GET"])
def toggle_bug():
    global BUG_IN_NEW_CHECKOUT
    val = request.args.get("enabled")
    if val is not None:
        BUG_IN_NEW_CHECKOUT = val.lower() in ("1", "true", "yes")
    else:
        BUG_IN_NEW_CHECKOUT = not BUG_IN_NEW_CHECKOUT
    logger.info(f"BUG_IN_NEW_CHECKOUT toggled to {BUG_IN_NEW_CHECKOUT}")
    return jsonify({"bug_enabled": BUG_IN_NEW_CHECKOUT}), 200

@app.route("/reset-metrics", methods=["POST", "GET"])
def reset_metrics():
    CHECKOUT_REQUESTS_TOTAL.clear()
    CHECKOUT_ERRORS_TOTAL.clear()
    for v in ("old_flow", "new_flow"):
        CHECKOUT_ERRORS_TOTAL.labels(variant=v)
        for s in ("200", "500"):
            CHECKOUT_REQUESTS_TOTAL.labels(variant=v, status=s)
    logger.info("Prometheus counter metrics cleared and zero-initialized")
    return jsonify({"status": "metrics_cleared"}), 200

@app.route("/health")
def health():
    return jsonify({
        "status": "healthy",
        "bug_enabled": BUG_IN_NEW_CHECKOUT,
        "flagsmith_api": FLAGSMITH_API_URL,
    }), 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
