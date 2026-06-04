<?php
/**
 * Import default wiki pages from wiki-pages/ directory.
 *
 * This maintenance script seeds the wiki with default pages (main page,
 * templates, CSS/JS) on first run. It only creates pages that don't
 * already exist, so manual wiki edits are preserved across restarts.
 *
 * Usage: php maintenance/run.php /var/www/html/import-pages.php
 */

require_once __DIR__ . '/maintenance/Maintenance.php';

use MediaWiki\Title\Title;
use MediaWiki\Revision\SlotRecord;

class ImportDefaultPages extends Maintenance {

    public function __construct() {
        parent::__construct();
        $this->addDescription( 'Import default wiki pages from wiki-pages/ directory (create-if-missing)' );
        $this->addOption( 'force', 'Overwrite existing pages (used on fresh install)', false, false );
    }

    /**
     * Mapping from filename to wiki page title.
     * Filenames use underscores; titles use proper MediaWiki naming.
     */
    private function getPageMapping(): array {
        return [
            // System pages
            'MediaWiki_Common.css'       => 'MediaWiki:Common.css',
            'MediaWiki_Common.js'        => 'MediaWiki:Common.js',
            'MediaWiki_Mainpage'         => 'MediaWiki:Mainpage',
            'MediaWiki_Sidebar'          => 'MediaWiki:Sidebar',

            // Main page
            'עמוד_ראשי'                  => 'עמוד ראשי',

            // Templates (תבנית = Template namespace in Hebrew)
            'תבנית_עמוד_ראשי_כותרת'      => 'תבנית:עמוד ראשי/כותרת',
            'תבנית_עמוד_ראשי_ניווט'      => 'תבנית:עמוד ראשי/ניווט',
            'תבנית_עמוד_ראשי_באנר'       => 'תבנית:עמוד ראשי/באנר',
            'תבנית_עמוד_ראשי_סטטיסטיקות' => 'תבנית:עמוד ראשי/סטטיסטיקות',
            'תבנית_עמוד_ראשי_ערך_מומלץ'  => 'תבנית:עמוד ראשי/ערך מומלץ',
            'תבנית_עמוד_ראשי_תמונה'      => 'תבנית:עמוד ראשי/תמונה',
            'תבנית_עמוד_ראשי_ציטוט'      => 'תבנית:עמוד ראשי/ציטוט',
            'תבנית_עמוד_ראשי_עונה_נוכחית' => 'תבנית:עמוד ראשי/עונה נוכחית',
            'תבנית_עמוד_ראשי_תארים'      => 'תבנית:עמוד ראשי/תארים',
            'תבנית_עמוד_ראשי_קישורים'    => 'תבנית:עמוד ראשי/קישורים',
            'תבנית_עמוד_ראשי_צור_קשר'    => 'תבנית:עמוד ראשי/צור קשר',
            'תבנית_עמוד_ראשי_הידעת'      => 'תבנית:עמוד ראשי/הידעת',

            // Cargo declaration templates
            'תבנית_Cargo_FanAnthem'       => 'תבנית:Cargo/FanAnthem',
            'תבנית_Cargo_MuseumItem'      => 'תבנית:Cargo/MuseumItem',
            'תבנית_Cargo_Kit'             => 'תבנית:Cargo/Kit',
            'תבנית_Cargo_FanStory'        => 'תבנית:Cargo/FanStory',
            'תבנית_Cargo_Season'          => 'תבנית:Cargo/Season',

            // Infobox templates (used by sample data pages for #cargo_store)
            'תבנית_Fan_anthem_infobox'    => 'תבנית:Fan anthem infobox',
            'תבנית_Museum_item_infobox'   => 'תבנית:Museum item infobox',
            'תבנית_Kit_infobox'           => 'תבנית:Kit infobox',

            // Collection pages
            'שירי_קהל'                    => 'שירי קהל',
            'המוזיאון_הווירטואלי'          => 'המוזיאון הווירטואלי',
            'גלריית_מדים'                 => 'גלריית מדים',
            'סיפורי_אוהדים'               => 'סיפורי אוהדים',
            'ספר_השיאים'                  => 'ספר השיאים',
            'תרבות_אוהדים'                => 'תרבות אוהדים',
            'הידעת'                       => 'הידעת',

            // Card templates
            'תבנית_Anthem_card'           => 'תבנית:Anthem card',
            'תבנית_Museum_card'           => 'תבנית:Museum card',
            'תבנית_Kit_card'              => 'תבנית:Kit card',
            'תבנית_Fan_story_card'        => 'תבנית:Fan story card',

            // Sample data: Fan anthems
            'הנה_באנו'                    => 'הנה באנו',
            'אדום_זה_הצבע'               => 'אדום זה הצבע',
            'שער_שער_שער'                 => 'שער שער שער',

            // Sample data: Museum items
            'חולצת_אליפות_2016'           => 'חולצת אליפות 2016',
            'כרטיס_גמר_גביע_1997'        => 'כרטיס גמר גביע 1997',
            'מדליית_אליפות_2017'          => 'מדליית אליפות 2017',

            // Sample data: Kits
            'מדי_בית_2024'                => 'מדי בית 2024/25',
            'מדי_חוץ_2024'                => 'מדי חוץ 2024/25',
        ];
    }

    private const UPDATELOG_KEY = 'wiki7-pages-import';

    /**
     * Compute a deterministic SHA-256 hash over all mapped page files.
     * Changes to any file content, or adding/removing mapped files, will change the hash.
     */
    private function computePagesHash( string $pagesDir ): string {
        $mapping = $this->getPageMapping();
        $parts = [];
        ksort( $mapping );
        foreach ( $mapping as $filename => $pageTitle ) {
            $filePath = "$pagesDir/$filename";
            if ( file_exists( $filePath ) ) {
                $parts[] = "$filename=$pageTitle:" . sha1_file( $filePath );
            } else {
                $parts[] = "$filename=$pageTitle:MISSING";
            }
        }
        return hash( 'sha256', implode( "\n", $parts ) );
    }

    /**
     * Read the stored content hash from MediaWiki's updatelog table.
     */
    private function getStoredHash(): ?string {
        $dbr = $this->getServiceContainer()->getDBLoadBalancer()->getConnection( DB_REPLICA );
        try {
            $row = $dbr->selectRow(
                'updatelog',
                'ul_value',
                [ 'ul_key' => self::UPDATELOG_KEY ],
                __METHOD__
            );
            return $row ? $row->ul_value : null;
        } catch ( \Exception $e ) {
            // Table may not exist yet on very first run
            return null;
        }
    }

    /**
     * Store the content hash in MediaWiki's updatelog table.
     */
    private function storeHash( string $hash ): void {
        $dbw = $this->getServiceContainer()->getDBLoadBalancer()->getConnection( DB_PRIMARY );
        $dbw->upsert(
            'updatelog',
            [ 'ul_key' => self::UPDATELOG_KEY, 'ul_value' => $hash ],
            'ul_key',
            [ 'ul_value' => $hash ],
            __METHOD__
        );
    }

    public function execute() {
        $pagesDir = __DIR__ . '/wiki-pages';

        if ( !is_dir( $pagesDir ) ) {
            $this->error( "wiki-pages/ directory not found at: $pagesDir" );
            return;
        }

        $mapping = $this->getPageMapping();
        $force   = $this->hasOption( 'force' );

        // Auto-detect content changes via hash comparison
        if ( !$force ) {
            $currentHash = $this->computePagesHash( $pagesDir );
            $storedHash  = $this->getStoredHash();

            if ( $storedHash === null ) {
                $this->output( "  No stored content hash found — forcing import.\n" );
                $force = true;
            } elseif ( $storedHash !== $currentHash ) {
                $this->output( "  Content hash changed — forcing import.\n" );
                $force = true;
            } else {
                $this->output( "  Content hash unchanged — create-if-missing only.\n" );
            }
        }

        $created = 0;
        $updated = 0;
        $skipped = 0;
        $errors  = 0;

        if ( $force ) {
            $this->output( "  (force mode: overwriting existing pages)\n" );
        }

        foreach ( $mapping as $filename => $pageTitle ) {
            $filePath = "$pagesDir/$filename";

            if ( !file_exists( $filePath ) ) {
                $this->output( "  SKIP (file missing): $filename\n" );
                $skipped++;
                continue;
            }

            $title = Title::newFromText( $pageTitle );
            if ( !$title ) {
                $this->error( "  ERROR: Invalid title '$pageTitle'" );
                $errors++;
                continue;
            }

            $pageExists = $title->exists();

            // Skip existing pages unless --force is set
            if ( $pageExists && !$force ) {
                $this->output( "  EXISTS: $pageTitle\n" );
                $skipped++;
                continue;
            }

            $content = file_get_contents( $filePath );
            if ( $content === false ) {
                $this->error( "  ERROR: Cannot read file $filePath" );
                $errors++;
                continue;
            }

            // Determine content model based on page title
            $contentModel = $this->getContentModel( $pageTitle );

            try {
                $wikiPage = $this->getServiceContainer()
                    ->getWikiPageFactory()
                    ->newFromTitle( $title );

                $contentObj = $this->getServiceContainer()
                    ->getContentHandlerFactory()
                    ->getContentHandler( $contentModel )
                    ->unserializeContent( $content );

                $updater = $wikiPage->newPageUpdater(
                    $this->getServiceContainer()
                        ->getUserFactory()
                        ->newFromName( 'Admin' )
                );

                $updater->setContent( SlotRecord::MAIN, $contentObj );

                $editFlags = EDIT_SUPPRESS_RC;
                if ( !$pageExists ) {
                    $editFlags |= EDIT_NEW;
                }

                $comment = \MediaWiki\CommentStore\CommentStoreComment::newUnsavedComment(
                    $pageExists ? 'Auto-import: content update' : 'Auto-import: initial page creation'
                );

                $updater->saveRevision( $comment, $editFlags );

                if ( $updater->wasSuccessful() ) {
                    if ( $pageExists ) {
                        $this->output( "  UPDATED: $pageTitle\n" );
                        $updated++;
                    } else {
                        $this->output( "  CREATED: $pageTitle\n" );
                        $created++;
                    }
                } else {
                    $status = $updater->getStatus();
                    $this->error( "  ERROR creating '$pageTitle': " . $status->getMessage()->text() );
                    $errors++;
                }
            } catch ( \Exception $e ) {
                $this->error( "  ERROR creating '$pageTitle': " . $e->getMessage() );
                $errors++;
            }
        }

        // Store current hash so subsequent restarts can detect changes
        $finalHash = $this->computePagesHash( $pagesDir );
        $this->storeHash( $finalHash );

        $this->output( "\nImport complete: $created created, $updated updated, $skipped skipped, $errors errors.\n" );
    }

    /**
     * Determine the content model for a page based on its title.
     */
    private function getContentModel( string $pageTitle ): string {
        if ( str_ends_with( $pageTitle, '.css' ) ) {
            return CONTENT_MODEL_CSS;
        }
        if ( str_ends_with( $pageTitle, '.js' ) ) {
            return CONTENT_MODEL_JAVASCRIPT;
        }
        return CONTENT_MODEL_WIKITEXT;
    }
}

$maintClass = ImportDefaultPages::class;
require_once RUN_MAINTENANCE_IF_MAIN;
