#!/usr/bin/env bash
# Scan this repo with the local SonarQube: tests with coverage, then the scanner.
#
#   scripts/sonar.sh            results at http://127.0.0.1:9000/dashboard?id=tradevalidationscripts
#
# SonarQube runs on this machine, not in the cloud -- the repos are private and
# SonarCloud is only free for public ones. Start it once (it restarts with Docker):
#
#   docker run -d --name tradeval-sonarqube --restart unless-stopped -p 127.0.0.1:9000:9000 \
#     -v tradeval_sonar_data:/opt/sonarqube/data -v tradeval_sonar_extensions:/opt/sonarqube/extensions \
#     sonarqube:community
#
# The scan token is read from ~/.config/tradeval-sonar/token (or SONAR_TOKEN).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
SONAR_URL="${SONAR_HOST_URL:-http://127.0.0.1:9000}"
SONAR_TOKEN="${SONAR_TOKEN:-$(cat "${SONAR_TOKEN_FILE:-$HOME/.config/tradeval-sonar/token}" 2>/dev/null || true)}"
[ -n "$SONAR_TOKEN" ] || { echo "No scan token: set SONAR_TOKEN or write one to ~/.config/tradeval-sonar/token." >&2; exit 1; }
curl -fsS "$SONAR_URL/api/system/status" 2>/dev/null | grep -q '"UP"' \
    || { echo "SonarQube isn't answering at $SONAR_URL. Start it: docker start tradeval-sonarqube" >&2; exit 1; }

echo "== Tests with coverage"
.venv/bin/python -m pytest -q -p no:cacheprovider --cov=tradeval --cov-report=xml:coverage.xml >/dev/null \
    || echo "   Some tests failed; scanning anyway (the report still counts what ran)." >&2

echo "== Scan"
# Mounted at its own path, so the coverage report's file paths mean the same
# inside the scanner container as they do here.
# The token goes through the environment, never the command line, where any
# process listing would show it.
export SONAR_TOKEN SONAR_HOST_URL="${SONAR_URL/127.0.0.1/host.docker.internal}"
docker run --rm -e SONAR_TOKEN -e SONAR_HOST_URL \
    -v "$ROOT:$ROOT" -w "$ROOT" sonarsource/sonar-scanner-cli -Dsonar.projectBaseDir="$ROOT" -Dsonar.qualitygate.wait=false \
    | grep -E "ANALYSIS SUCCESSFUL|EXECUTION FAILURE|ERROR" || true
echo "   $SONAR_URL/dashboard?id=tradevalidationscripts"
