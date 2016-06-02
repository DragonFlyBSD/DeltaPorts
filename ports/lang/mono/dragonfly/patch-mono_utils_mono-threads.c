 Ease up concurent dports bulk builds on muscles.

--- mono/utils/mono-threads.c	2016-03-15 13:31:53.000000000 +0200
+++ mono/utils/mono-threads.c
@@ -61,8 +61,13 @@ static gboolean unified_suspend_enabled;
 
 /*warn at 50 ms*/
 #define SLEEP_DURATION_BEFORE_WARNING (10)
+#ifdef __DragonFly__
+/*abort at 1+ sec, building on idle system is fine, but fails too often on bulk dport runs*/
+#define SLEEP_DURATION_BEFORE_ABORT 666
+#else
 /*abort at 1 sec*/
 #define SLEEP_DURATION_BEFORE_ABORT 200
+#endif
 
 static int suspend_posts, resume_posts, abort_posts, waits_done, pending_ops;
 
