--- src/xdp-documents.c.orig
+++ src/xdp-documents.c
@@ -96,10 +96,18 @@
   basename = g_path_get_basename (path);
   dirname = g_path_get_dirname (path);
 
+#ifdef O_PATH
   if (flags & XDP_DOCUMENT_FLAG_FOR_SAVE)
     fd = open (dirname, O_PATH | O_CLOEXEC);
   else
     fd = open (path, O_PATH | O_CLOEXEC);
+#else
+  /* DragonFly has no O_PATH; open the target directly instead. */
+  if (flags & XDP_DOCUMENT_FLAG_FOR_SAVE)
+    fd = open (dirname, O_RDONLY | O_DIRECTORY | O_CLOEXEC);
+  else
+    fd = open (path, O_RDONLY | O_CLOEXEC);
+#endif
 
   if (fd == -1)
     {
