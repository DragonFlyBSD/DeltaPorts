--- extensions/node_modules/@parcel/watcher/src/kqueue/KqueueBackend.cc.orig
+++ extensions/node_modules/@parcel/watcher/src/kqueue/KqueueBackend.cc
@@ -191,7 +191,7 @@
 bool KqueueBackend::compareDir(int fd, std::string &path, std::unordered_set<WatcherRef> &watchers) {
   // macOS doesn't support fdclosedir, so we have to duplicate the file descriptor
   // to ensure the closedir doesn't also stop watching.
-  #if __APPLE__
+  #if __APPLE__ || defined(__DragonFly__)
     fd = dup(fd);
   #endif
 
@@ -279,7 +279,7 @@
     }
   }
 
-  #if __APPLE__
+  #if __APPLE__ || defined(__DragonFly__)
     closedir(dir);
   #else
     fdclosedir(dir);
