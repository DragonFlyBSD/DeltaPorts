--- dom/media/mediacontrol/AudioSessionManager.cpp.orig
+++ dom/media/mediacontrol/AudioSessionManager.cpp
@@ -7,7 +7,7 @@
 #include "MediaControlUtils.h"
 #include "MediaController.h"
 #include "mozilla/ScopeExit.h"
-#include "mozilla/Uptime.h"
+#include "mozilla/TimeStamp.h"
 #include "mozilla/glean/DomMediaMetrics.h"
 
 #undef LOG
@@ -74,12 +74,13 @@ void AudioSessionManager::NotifyAudibilityChanged(uint6
     // Lazy-create on the first audible transition. The audibility timestamp
     // must be set before the §5.2 mutator below transitions mState to Active,
     // to satisfy the "active requires audible" invariant on the record.
-    Maybe<int64_t> uptime = mozilla::ProcessUptimeMs();
-    MOZ_DIAGNOSTIC_ASSERT(uptime.isSome(),
-                          "ProcessUptimeMs should always have a value "
-                          "during audibility transitions");
+    // Session selection only compares timestamps within this process. Use
+    // the portable monotonic clock: ProcessUptimeMs() can be Nothing() on
+    // platforms without CLOCK_BOOTTIME, including DragonFly.
+    const int64_t uptime = static_cast<int64_t>(
+        (TimeStamp::Now() - TimeStamp::ProcessCreation()).ToMilliseconds());
     mAudioSessions.LookupOrInsert(aBrowsingContextId)
-        .SetAudibleAtMs(aBrowsingContextId, Some(*uptime));
+        .SetAudibleAtMs(aBrowsingContextId, Some(uptime));
   } else if (bcWasAudible && !bcIsAudibleNow) {
     existing.Data().SetAudibleAtMs(aBrowsingContextId, Nothing());
   }
