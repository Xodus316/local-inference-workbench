DECISION_32K = "pplx-decider:review-32k"
DECISION_64K = "pplx-decider:review-64k"
DECISION_CONFIDENCE_MINIMUM = 0.75



def remote_model(name, metadata):
    return bool(name.lower().endswith(":cloud") or metadata.get("remote_host") or metadata.get("remote_model"))
