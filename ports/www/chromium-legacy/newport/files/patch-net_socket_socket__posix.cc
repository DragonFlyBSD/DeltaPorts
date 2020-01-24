--- net/socket/socket_posix.cc.orig	2019-10-21 19:06:38 UTC
+++ net/socket/socket_posix.cc
@@ -520,7 +520,7 @@ void SocketPosix::ReadCompleted() {
 }
 
 int SocketPosix::DoWrite(IOBuffer* buf, int buf_len) {
-#if defined(OS_LINUX) || defined(OS_ANDROID)
+#if defined(OS_LINUX) || defined(OS_ANDROID) || defined(OS_BSD)
   // Disable SIGPIPE for this write. Although Chromium globally disables
   // SIGPIPE, the net stack may be used in other consumers which do not do
   // this. MSG_NOSIGNAL is a Linux-only API. On OS X, this is a setsockopt on
