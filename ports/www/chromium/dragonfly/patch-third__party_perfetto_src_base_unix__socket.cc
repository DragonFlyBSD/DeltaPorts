diff --git third_party/perfetto/src/base/unix_socket.cc third_party/perfetto/src/base/unix_socket.cc
index ac20eabcefe5..cbda230e5eb3 100644
--- third_party/perfetto/src/base/unix_socket.cc
+++ third_party/perfetto/src/base/unix_socket.cc
@@ -45,7 +45,7 @@
 #include <unistd.h>
 #endif
 
-#if PERFETTO_BUILDFLAG(PERFETTO_OS_APPLE) || defined(__FreeBSD__)
+#if PERFETTO_BUILDFLAG(PERFETTO_OS_APPLE) || defined(__FreeBSD__) || defined(__DragonFly__)
 #include <sys/ucred.h>
 #endif
 
@@ -1031,8 +1031,8 @@ void UnixSocket::ReadPeerCredentialsPosix() {
   int res = getpeereid(fd, &peer_uid_, nullptr);
   PERFETTO_CHECK(res == 0);
   // There is no pid when obtaining peer credentials for QNX
-#elif !defined(__FreeBSD__) && PERFETTO_BUILDFLAG(PERFETTO_OS_LINUX) || \
-    PERFETTO_BUILDFLAG(PERFETTO_OS_ANDROID)
+#elif (!defined(__FreeBSD__) && !defined(__DragonFly__)) && PERFETTO_BUILDFLAG(PERFETTO_OS_LINUX) || \
+      PERFETTO_BUILDFLAG(PERFETTO_OS_ANDROID)
 #if PERFETTO_BUILDFLAG(PERFETTO_OS_BSD)
   struct sockpeercred user_cred;
 #else
@@ -1041,6 +1041,7 @@ void UnixSocket::ReadPeerCredentialsPosix() {
   socklen_t len = sizeof(user_cred);
   int fd = sock_raw_.fd();
   int res = getsockopt(fd, SOL_SOCKET, SO_PEERCRED, &user_cred, &len);
+
   PERFETTO_CHECK(res == 0);
   peer_uid_ = user_cred.uid;
   peer_pid_ = user_cred.pid;
