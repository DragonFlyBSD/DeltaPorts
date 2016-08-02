--- src/task/task.c.intermediate	2016-08-02 13:26:37 UTC
+++ src/task/task.c
@@ -313,7 +313,7 @@ tns_value_t *taskgetinfo(void)
 
 static int taskargc;
 static char **taskargv;
-#if defined(__FreeBSD__)
+#if defined(__FreeBSD__) || defined(__DragonFly__)
 int MAINSTACKSIZE = 96 * 1024;
 #else
 int MAINSTACKSIZE = 32 * 1024;
