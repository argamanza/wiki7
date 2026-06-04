FROM mediawiki:1.45.3

# Install tools for image handling and extensions
RUN apt-get update && apt-get install -y \
    git unzip wget imagemagick mariadb-client \
    && rm -rf /var/lib/apt/lists/*

# Download Composer
RUN wget -cO - https://getcomposer.org/composer-2.phar > composer.phar \
    && mv composer.phar /usr/local/bin/composer \
    && chmod +x /usr/local/bin/composer

# Clone and configure the AWS extension
ENV COMPOSER_ROOT_VERSION=1.45.0
RUN cd /var/www/html && \
git clone --depth 1 https://github.com/edwardspec/mediawiki-aws-s3.git extensions/AWS && \
echo '{"extra":{"merge-plugin":{"include":["extensions/AWS/composer.json"]}}}' > composer.local.json && \
composer config --global audit.block-insecure false && \
composer update --no-dev --no-audit

# Set working directory
WORKDIR /var/www/html

# Copy .htaccess
COPY .htaccess /var/www/html/.htaccess

# Copy LocalSettings.php
COPY LocalSettings.php /var/www/html/LocalSettings.php

# Install TabberNeue extension (tabbed content for stats, lyrics, galleries)
RUN git clone --depth 1 https://github.com/StarCitizenTools/mediawiki-extensions-TabberNeue.git extensions/TabberNeue

# Copy extensions
COPY extensions/Cargo/ /var/www/html/extensions/Cargo/
COPY extensions/PageForms/ /var/www/html/extensions/PageForms/

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