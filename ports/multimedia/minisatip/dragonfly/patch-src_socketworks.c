--- src/socketworks.c.orig	2021-09-12 22:54:16 UTC
+++ src/socketworks.c
@@ -1217,7 +1217,7 @@ pthread_t get_socket_thread(int s_id) {
 #undef DEFAULT_LOG
 #define DEFAULT_LOG LOG_SOCKET
 
-#ifdef __APPLE__
+#if defined(__APPLE__) || defined(__DragonFly__)
 struct mmsghdr {
     struct msghdr msg_hdr; /* Message header */
     unsigned int msg_len;  /* Number of bytes transmitted */
