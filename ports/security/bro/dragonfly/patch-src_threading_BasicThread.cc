--- src/threading/BasicThread.cc.orig	2018-12-19 16:36:16 UTC
+++ src/threading/BasicThread.cc
@@ -64,7 +64,7 @@ void BasicThread::SetOSName(const char*
 	pthread_setname_np(arg_name);
 #endif
 
-#ifdef __FreeBSD__
+#ifdef __FreeBSD__ || defined __DragonFly__
 	pthread_set_name_np(thread.native_handle(), arg_name);
 #endif
 	}
