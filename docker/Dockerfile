FROM mediawiki:1.45.3

# Install tools for image handling and extensions
RUN apt-get update && apt-get install -y \
    git unzip wget imagemagick mariadb-client \
    && rm -rf /var/lib/apt/lists/*

# Install the PECL redis PHP extension so MediaWiki's RedisBagOStuff can connect
# to a sidecar Redis container ($wgMainCacheType / $wgParserCacheType in
# LocalSettings.php). $PHPIZE_DEPS is set by the docker-php base image.
RUN apt-get update \
    && apt-get install -y --no-install-recommends $PHPIZE_DEPS \
    && pecl install redis \
    && docker-php-ext-enable redis \
    && apt-get purge -y --auto-remove $PHPIZE_DEPS \
    && rm -rf /var/lib/apt/lists/*

# Download Composer
RUN wget -cO - https://getcomposer.org/composer-2.phar > composer.phar \
    && mv composer.phar /usr/local/bin/composer \
    && chmod +x /usr/local/bin/composer

# Clone and configure the AWS extension (pinned for reproducibility)
ENV COMPOSER_ROOT_VERSION=1.45.0
ARG AWS_S3_EXT_TAG=v0.14.0
RUN cd /var/www/html && \
git clone --depth 1 --branch "$AWS_S3_EXT_TAG" https://github.com/edwardspec/mediawiki-aws-s3.git extensions/AWS && \
echo '{"extra":{"merge-plugin":{"include":["extensions/AWS/composer.json"]}}}' > composer.local.json && \
composer config --global audit.block-insecure false && \
composer update --no-dev --no-audit

# Set working directory
WORKDIR /var/www/html

# Copy .htaccess
COPY .htaccess /var/www/html/.htaccess

# Static robots.txt - without this, the .htaccess rewrite would route /robots.txt
# through index.php and MW would treat it as a page title. The .htaccess rule
# skips real files, so an actual robots.txt at the docroot is served as-is.
COPY robots.txt /var/www/html/robots.txt

# Static /favicon.ico at the docroot. $wgFavicon below makes MW emit a <link rel="icon">
# pointing at /assets/favicon.ico (S3-backed via CloudFront), but browsers also auto-fetch
# /favicon.ico without consulting the HTML - bookmark/feed/preview tools especially. Same
# .htaccess passthrough logic as robots.txt: real file at the docroot wins over the
# index.php rewrite.
COPY assets/favicon.ico /var/www/html/favicon.ico

# Copy LocalSettings.php
COPY LocalSettings.php /var/www/html/LocalSettings.php

# Install TabberNeue extension (tabbed content for stats, lyrics, galleries; pinned)
ARG TABBERNEUE_TAG=v3.4.1
RUN git clone --depth 1 --branch "$TABBERNEUE_TAG" https://github.com/StarCitizenTools/mediawiki-extensions-TabberNeue.git extensions/TabberNeue

# SEO extensions:
#  - Description2 extracts a per-page meta `description` page property from the article's
#    leading paragraph; WikiSEO reads its output when present.
#  - WikiSEO emits the full OG + Twitter Card + Schema.org JSON-LD + canonical-link
#    surface from page metadata and the per-page {{#seo:title=|description=|image=|type=}}
#    parser function. Supersedes the previous OpenGraphMeta+custom-hook approach: gives
#    us Twitter Cards, structured data, and a single config-driven story instead of
#    glue PHP in LocalSettings.php.
# Both pinned to REL branches matching the MediaWiki major version (REL1_45 for MW 1.45).
# Earlier REL1_43 pin on OpenGraphMeta called wfExpandUrl(), which is fatal-on-call in
# MW 1.45 - every homepage render 500'd. Lesson: match the REL branch.
ARG DESCRIPTION2_BRANCH=REL1_45
RUN git clone --depth 1 --branch "$DESCRIPTION2_BRANCH" https://github.com/wikimedia/mediawiki-extensions-Description2.git extensions/Description2
ARG WIKISEO_BRANCH=REL1_45
RUN git clone --depth 1 --branch "$WIKISEO_BRANCH" https://github.com/wikimedia/mediawiki-extensions-WikiSEO.git extensions/WikiSEO

# Phase 3.5 review-gate extensions (see docs/adr/0002-review-gate-architecture.md):
#  - ApprovedRevs: holds the bot's UPDATE revisions back from public until a reviewer
#    explicitly approves. Public continues to see the previously-approved revision.
#  - Lockdown: gates read+edit on the custom Draft namespace (NS_DRAFT=3000) to the
#    'reviewer' group only — bot writes new pages here, invisible to public.
# Both pinned to REL1_45 (verified branches exist upstream 2026-06-08).
# (Echo is already bundled with MediaWiki 1.45 core — loaded directly in LocalSettings.php.)
ARG APPROVEDREVS_BRANCH=REL1_45
RUN git clone --depth 1 --branch "$APPROVEDREVS_BRANCH" https://github.com/wikimedia/mediawiki-extensions-ApprovedRevs.git extensions/ApprovedRevs
ARG LOCKDOWN_BRANCH=REL1_45
RUN git clone --depth 1 --branch "$LOCKDOWN_BRANCH" https://github.com/wikimedia/mediawiki-extensions-Lockdown.git extensions/Lockdown

# Copy extensions
COPY extensions/Cargo/ /var/www/html/extensions/Cargo/
COPY extensions/PageForms/ /var/www/html/extensions/PageForms/
COPY extensions/Wiki7ReviewGate/ /var/www/html/extensions/Wiki7ReviewGate/

# Copy skins
COPY skins/Citizen/ /var/www/html/skins/Citizen/
COPY skins/Wiki7/ /var/www/html/skins/Wiki7/

# Copy wiki page seed files and import script
COPY wiki-pages/ /var/www/html/wiki-pages/
COPY import-pages.php /var/www/html/import-pages.php

# Back up custom LocalSettings.php so entrypoint can restore it after install.php
RUN cp /var/www/html/LocalSettings.php /var/www/html/LocalSettings.php.custom

# Copy entrypoint script
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# Fix permissions
RUN chown -R www-data:www-data /var/www/html

ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["apache2-foreground"]