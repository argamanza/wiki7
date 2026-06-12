#!/bin/bash
set -e

# Safety net: whatever happens below (install.php dying midway, set -e abort),
# the container must never be left running — or restarting — with the
# installer-generated LocalSettings.php instead of our custom one. The trap
# restores the custom file on every exit path; on the success path the exec
# at the end replaces the shell so the trap never fires (the custom file is
# already in place by then). The cp at boot also self-heals a previous crash
# that stranded the wrong config.
restore_custom_localsettings() {
  if [ -f /var/www/html/LocalSettings.php.custom ]; then
    cp /var/www/html/LocalSettings.php.custom /var/www/html/LocalSettings.php
  fi
}
trap restore_custom_localsettings EXIT
restore_custom_localsettings

# 1. Wait for DB to be reachable (max 60s)
echo "Waiting for database..."
for i in $(seq 1 30); do
  if mysqladmin ping -h "$MEDIAWIKI_DB_HOST" -u "$MEDIAWIKI_DB_USER" \
     -p"$MEDIAWIKI_DB_PASSWORD" --skip-ssl --silent 2>/dev/null; then
    echo "Database is ready."
    break
  fi
  if [ "$i" -eq 30 ]; then
    echo "ERROR: Database not reachable after 60s. Exiting."
    exit 1
  fi
  echo "Attempt $i/30 — DB not ready, waiting 2s..."
  sleep 2
done

# 2. Check if MediaWiki is already installed (core `page` table exists)
TABLE_EXISTS=$(mysql -h "$MEDIAWIKI_DB_HOST" -u "$MEDIAWIKI_DB_USER" \
  -p"$MEDIAWIKI_DB_PASSWORD" --skip-ssl "$MEDIAWIKI_DB_NAME" \
  -sse "SELECT COUNT(*) FROM information_schema.tables \
        WHERE table_schema='$MEDIAWIKI_DB_NAME' AND table_name='page';" 2>/dev/null || echo "0")

if [ "$TABLE_EXISTS" = "0" ]; then
  echo "=== Fresh database detected. Running MediaWiki install... ==="
  # install.php refuses to run if LocalSettings.php exists — move it aside
  mv /var/www/html/LocalSettings.php /var/www/html/LocalSettings.php.bak

  php maintenance/run.php install \
    --dbserver="$MEDIAWIKI_DB_HOST" \
    --dbname="$MEDIAWIKI_DB_NAME" \
    --dbuser="$MEDIAWIKI_DB_USER" \
    --dbpass="$MEDIAWIKI_DB_PASSWORD" \
    --server="https://wiki7.co.il" \
    --scriptpath="" \
    --lang=he \
    --pass="$MEDIAWIKI_ADMIN_PASSWORD" \
    "ויקישבע" "Admin"

  # install.php generates a new LocalSettings.php — restore our custom one
  cp /var/www/html/LocalSettings.php.custom /var/www/html/LocalSettings.php
  echo "=== Install complete. Restored custom LocalSettings.php ==="
fi

# 3. Always run update.php (idempotent — handles schema migrations for extensions)
echo "=== Running update.php for schema migrations... ==="
php maintenance/run.php update --quick

# 4. Import default wiki pages (main page, templates, CSS/JS)
#    Auto-detects content changes via hash comparison in updatelog table
echo "=== Importing default wiki pages... ==="
php maintenance/run.php /var/www/html/import-pages.php

echo "=== Database initialization complete. Starting Apache... ==="

# 5. Hand off to Apache
exec docker-php-entrypoint apache2-foreground
