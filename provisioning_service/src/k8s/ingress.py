"""Ingress management for shared ALB

The shared ALB Ingress (openclaw-provisioning-public) is now created by the
deployment script (05-deploy-application-stack-db.sh) instead of at app startup.
OpenClaw instance Ingresses join the same ALB group via config in instance.py.
"""
