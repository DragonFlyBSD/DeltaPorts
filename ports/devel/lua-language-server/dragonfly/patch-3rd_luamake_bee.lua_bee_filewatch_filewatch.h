--- 3rd/luamake/bee.lua/bee/filewatch/filewatch.h.orig	2026-11-08 00:00:00.000000000 +0000
+++ 3rd/luamake/bee.lua/bee/filewatch/filewatch.h	2026-11-08 00:00:00.000000000 +0000
@@ -12,7 +12,7 @@

 #    include <mutex>
 #    include <set>
-#elif defined(__linux__) || defined(__FreeBSD__) || defined(__NetBSD__) || defined(__OpenBSD__)
+#elif defined(__linux__) || defined(__FreeBSD__) || defined(__DragonFly__) || defined(__NetBSD__) || defined(__OpenBSD__)
 #    include <map>
 #else
 #    error unsupport platform
@@ -64,7 +64,7 @@
         bool create_stream(CFArrayRef cf_paths) noexcept;
         void destroy_stream() noexcept;
         void update_stream() noexcept;
-#elif defined(__linux__) || defined(__FreeBSD__) || defined(__NetBSD__) || defined(__OpenBSD__)
+#elif defined(__linux__) || defined(__FreeBSD__) || defined(__DragonFly__) || defined(__NetBSD__) || defined(__OpenBSD__)
         void event_update(void* event) noexcept;
 #endif

@@ -78,7 +78,7 @@
         std::set<std::string> m_paths;
         FSEventStreamRef m_stream;
         dispatch_queue_t m_fsevent_queue;
-#elif defined(__linux__) || defined(__FreeBSD__) || defined(__NetBSD__) || defined(__OpenBSD__)
+#elif defined(__linux__) || defined(__FreeBSD__) || defined(__DragonFly__) || defined(__NetBSD__) || defined(__OpenBSD__)
         std::map<int, std::string> m_fd_path;
         int m_inotify_fd;
         bool m_follow_symlinks = false;
