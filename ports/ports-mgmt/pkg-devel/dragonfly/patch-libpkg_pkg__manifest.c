--- libpkg/pkg_manifest.c.orig	2013-11-19 18:52:23.000000000 +0000
+++ libpkg/pkg_manifest.c
@@ -807,6 +807,8 @@ emit_manifest(struct pkg *pkg, char **ou
 	const char *script_types = NULL;
 	lic_t licenselogic;
 	int64_t flatsize, pkgsize;
+#pragma GCC diagnostic push
+#pragma GCC diagnostic ignored "-Wunused-but-set-variable"
 	ucl_object_t *obj = NULL, *map, *seq, *submap;
 	ucl_object_t *top = NULL;
 
@@ -995,6 +997,7 @@ emit_manifest(struct pkg *pkg, char **ou
 		    ucl_object_fromstring_common(sbuf_data(tmpsbuf), sbuf_len(tmpsbuf), UCL_STRING_TRIM),
 		    "message", 7, false);
 	}
+#pragma GCC diagnostic pop
 
 	if ((flags & PKG_MANIFEST_EMIT_PRETTY) == PKG_MANIFEST_EMIT_PRETTY)
 		*out = ucl_object_emit(top, UCL_EMIT_YAML);
