--- libpkg/update.c.orig	2013-11-19 18:52:23.000000000 +0000
+++ libpkg/update.c
@@ -581,7 +581,7 @@ pkg_update_incremental(const char *name,
 	sqlite3 *sqlite = NULL;
 	struct pkg *pkg = NULL;
 	int rc = EPKG_FATAL;
-	const char *origin, *digest, *offset, *length, *files_offset;
+	const char *origin, *digest, *offset, *length;
 	struct pkgdb_it *it = NULL;
 	char *linebuf = NULL, *p;
 	int updated = 0, removed = 0, added = 0, processed = 0;
@@ -640,7 +640,7 @@ pkg_update_incremental(const char *name,
 		origin = strsep(&p, ":");
 		digest = strsep(&p, ":");
 		offset = strsep(&p, ":");
-		files_offset = strsep(&p, ":");
+		strsep(&p, ":");
 		length = strsep(&p, ":");
 
 		if (origin == NULL || digest == NULL ||
