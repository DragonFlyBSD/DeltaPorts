--- ipc/chromium/src/base/shared_memory_posix.cc.ori	Mon Mar  3 15:15:51 2025
+++ ipc/chromium/src/base/shared_memory_posix.cc	Mon Mar  3 15:16:20 2025
@@ -284,6 +284,11 @@ bool SharedMemory::AppendPosixShmPrefix(std::string* s
     StringAppendF(str, "snap.%s.", snap);
   }
 #  endif  // XP_LINUX
+#ifdef OS_DRAGONFLY
+  // DragonFly BSD has a userland IPC implementation, we need to prefix the
+  // path to shm_open(3), preferably with '/tmp'
+  StringAppendF(str, "tmp/");
+#endif
   // Hopefully the "implementation defined" name length limit is long
   // enough for this.
   StringAppendF(str, "org.mozilla.ipc.%d.", static_cast<int>(pid));
