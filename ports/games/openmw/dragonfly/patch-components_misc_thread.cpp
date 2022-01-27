--- components/misc/thread.cpp.orig	2021-10-10 16:17:03 UTC
+++ components/misc/thread.cpp
@@ -41,7 +41,7 @@ namespace Misc
     }
 }
 
-#elif defined(__FreeBSD__)
+#elif defined(__FreeBSD__) || defined(__DragonFly__)
 
 #include <sys/types.h>
 #include <sys/rtprio.h>
@@ -53,6 +53,9 @@ namespace Misc
         struct rtprio prio;
         prio.type = RTP_PRIO_IDLE;
         prio.prio = RTP_PRIO_MAX;
+#ifdef __DragonFly__
+#define rtprio_thread lwp_rtprio
+#endif
         if (rtprio_thread(RTP_SET, 0, &prio) == 0)
             Log(Debug::Verbose) << "Using idle priority for thread=" << std::this_thread::get_id();
         else
