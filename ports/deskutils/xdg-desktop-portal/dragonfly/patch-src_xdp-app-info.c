--- src/xdp-app-info.c.orig
+++ src/xdp-app-info.c
@@ -585,6 +585,7 @@
   if (path == NULL)
     return NULL;
 
+#ifdef O_PATH
   if ((fd_flags & O_PATH) == O_PATH)
     {
       int read_access_mode;
@@ -634,6 +635,7 @@
         writable = TRUE;
     }
   else /* Regular file with no O_PATH */
+#endif
     {
       int accmode = fd_flags & O_ACCMODE;
 
