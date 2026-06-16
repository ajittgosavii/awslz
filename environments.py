"""Single source of truth for the three workload environments.

Prevents the label/colour drift that previously existed between the diagrams
(arch_diagram) and the playbooks (Production/Stage/Dev vs Prod/Staging/Dev+Test).
"""

from colors import AMBER, BLUE, RED

# Ordered Prod -> Staging -> Dev+Test. `cidr_base` is the us-east-1 /20 base used
# by the multi-account diagram (us-west-2 mirror is the 10.1->10.2 swap).
ENVIRONMENTS = [
    {"key": "prod",  "label": "Prod",     "color": RED,   "flow": "flow-prod", "cidr_base": "10.120"},
    {"key": "stage", "label": "Staging",  "color": AMBER, "flow": "flow-stg",  "cidr_base": "10.140"},
    {"key": "dev",   "label": "Dev+Test", "color": BLUE,  "flow": "flow-np",   "cidr_base": "10.160"},
]

ENV_LABELS = [e["label"] for e in ENVIRONMENTS]                  # ["Prod", "Staging", "Dev+Test"]
ENV_LABEL_TO_KEY = {e["label"]: e["key"] for e in ENVIRONMENTS}  # {"Prod": "prod", ...}
