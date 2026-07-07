# Re:putation production target for the existing MotionLabs GCP project.
#
# Copy to terraform.tfvars only when deploying to mso-platform-481505.
# Secret values must stay out of Terraform state; create Secret Manager versions
# out of band as described in docs/plans/2026-06-12-gcp-deployment-prep.md.

project_id = "mso-platform-481505"
region     = "asia-northeast3"
zone       = "asia-northeast3-a"

# Public customer-facing domains. DNS is hosted outside this GCP project today,
# so Terraform must not try to manage Cloud DNS records.
domain          = "reputation.motionlabs.kr"
admin_subdomain = "admin.reputation.motionlabs.kr"
dns_zone_name   = ""
cname_target    = "cname.reputation.motionlabs.kr"

# Current hospital-owned custom domains known to production. When
# use_certificate_map=true, this list is informational for legacy direct certs;
# Certificate Manager serving is driven by certificate_map_customer_domains.
customer_domains = ["jangclinic.kr"]

# Customer-owned domains that should receive Certificate Manager map entries.
# Add a domain only after its DNS authorization CNAME exists and its
# Certificate Manager cert can become ACTIVE.
certificate_map_customer_domains = ["jangclinic.kr"]

# Production HTTPS proxy now serves through Certificate Manager certificate map.
# Keep this true unless rolling back deliberately.
use_certificate_map = true

# Keep launch scale deliberately bounded. Raise after DB connection metrics and
# request latency justify it.
api_min_instances    = 0
api_max_instances    = 2
site_min_instances   = 0
site_max_instances   = 1
admin_min_instances  = 0
admin_max_instances  = 1
worker_min_instances = 1
worker_max_instances = 2
beat_min_instances   = 1
beat_max_instances   = 1
beat_memory          = "512Mi"

# Cost-balanced launch defaults. Cloud SQL and Redis are the main always-on
# cost centers; Cloud Run public services scale to zero except worker/beat.
db_edition           = "ENTERPRISE"
db_instance_tier     = "db-custom-1-3840"
redis_memory_size_gb = 1

# Deferred: project is at the global IN_USE_ADDRESSES quota (8/8 as of 2026-06-21).
# Every global forwarding rule consumes one address slot even when it reuses the
# LB IP, so the port-80 redirect rule has no slot. Keep HTTPS (443) first; re-enable
# after raising the IN_USE_ADDRESSES quota (not the forwarding-rule quota, which has
# headroom) or retiring an unused global LB. Not a launch blocker — HTTPS serves directly.
enable_http_redirect = false

# Keep generated assets and reports in the same metro as the runtime to avoid
# unnecessary latency and cross-region storage reads.
images_bucket_location  = "ASIA-NORTHEAST3"
reports_bucket_location = "ASIA-NORTHEAST3"

site_revalidate_url = "https://reputation.motionlabs.kr/api/revalidate"

# Optional after a real ops mailbox/channel is confirmed.
# alert_email = "ops@motionlabs.kr"
# notification_channels = [
#   "projects/mso-platform-481505/notificationChannels/<channel-id>"
# ]

# Set to immutable digests during the bootstrap/apply step.
# api_image   = "asia-northeast3-docker.pkg.dev/mso-platform-481505/reputation/reputation@sha256:<digest>"
# site_image  = "asia-northeast3-docker.pkg.dev/mso-platform-481505/reputation/site@sha256:<digest>"
# admin_image = "asia-northeast3-docker.pkg.dev/mso-platform-481505/reputation/admin@sha256:<digest>"
