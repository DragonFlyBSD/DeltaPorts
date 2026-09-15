--- src/camel/camel-debug.c.orig
+++ src/camel/camel-debug.c
@@ -1074,7 +1074,7 @@
 	mlink = NULL;
 	lt = lines_tolerance;
 	do {
-  try_next_frame:
+  try_next_frame:;
 		gboolean found = FALSE;
 		BacktraceLine *fline = flink->data;
 
