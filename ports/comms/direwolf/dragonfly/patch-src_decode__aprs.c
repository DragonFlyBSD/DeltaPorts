--- src/decode_aprs.c.orig	2020-10-28 00:45:30 UTC
+++ src/decode_aprs.c
@@ -3930,7 +3930,7 @@ static void decode_tocall (decode_aprs_t
  * models before getting to the more generic APY.
  */
 
-#if defined(__WIN32__) || defined(__OpenBSD__) || defined(__FreeBSD__) || defined(__APPLE__)
+#if defined(__WIN32__) || defined(__OpenBSD__) || defined(__FreeBSD__) || defined(__APPLE__) || defined(__DragonFly__)
 	    qsort (tocalls, num_tocalls, sizeof(struct tocalls_s), tocall_cmp);
 #else
 	    qsort (tocalls, num_tocalls, sizeof(struct tocalls_s), (__compar_fn_t)tocall_cmp);
