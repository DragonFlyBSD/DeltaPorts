--- va/va_trace.c.intermediate	2016-06-29 19:04:28 UTC
+++ va/va_trace.c
@@ -299,6 +299,8 @@ static void add_trace_config_info(
     int idx = 0;
 #ifdef __FreeBSD__
     pid_t thd_id = pthread_getthreadid_np();
+#elif defined(__DragonFly__)
+    pid_t thd_id = syscall(SYS_lwp_gettid);
 #else
     pid_t thd_id = syscall(__NR_gettid);
 #endif
@@ -327,6 +329,8 @@ static void delete_trace_config_info(
     int idx = 0;
 #ifdef __FreeBSD__
     pid_t thd_id = pthread_getthreadid_np();
+#elif defined(__DragonFly__)
+    pid_t thd_id = syscall(SYS_lwp_gettid);
 #else
     pid_t thd_id = syscall(__NR_gettid);
 #endif
@@ -676,6 +680,8 @@ static struct trace_log_file *start_trac
     struct trace_log_file *plog_file = NULL;
 #ifdef __FreeBSD__
     pid_t thd_id = pthread_getthreadid_np();
+#elif defined(__DragonFly__)
+    pid_t thd_id = syscall(SYS_lwp_gettid);
 #else
     pid_t thd_id = syscall(__NR_gettid);
 #endif
@@ -719,6 +725,8 @@ static void refresh_log_file(
     struct trace_log_file *plog_file = NULL;
 #ifdef __FreeBSD__
     pid_t thd_id = pthread_getthreadid_np();
+#elif defined(__DragonFly__)
+    pid_t thd_id = syscall(SYS_lwp_gettid);
 #else
     pid_t thd_id = syscall(__NR_gettid);
 #endif
@@ -1247,6 +1255,8 @@ static void internal_TraceUpdateContext
     int i = 0, delete = 1;
 #ifdef __FreeBSD__
     pid_t thd_id = pthread_getthreadid_np();
+#elif defined(__DragonFly__)
+    pid_t thd_id = syscall(SYS_lwp_gettid);
 #else
     pid_t thd_id = syscall(__NR_gettid);
 #endif
