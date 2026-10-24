--- loguru.cpp.orig
+++ loguru.cpp
@@ -1100,6 +1100,8 @@
 			#elif defined(__FreeBSD__)
 				long thread_id;
 				(void)thr_self(&thread_id);
+			#elif defined(__DragonFly__)
+				long thread_id = lwp_gettid();
 			#elif LOGURU_PTHREADS
 				uint64_t thread_id = pthread_self();
 			#else
