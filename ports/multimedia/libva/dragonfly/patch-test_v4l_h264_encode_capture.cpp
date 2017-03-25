--- test/v4l_h264/encode/capture.cpp.intermediate	2016-06-29 19:12:12 UTC
+++ test/v4l_h264/encode/capture.cpp
@@ -38,7 +38,7 @@
 #include <fcntl.h> /* low-level i/o */
 #include <errno.h>
 #include <unistd.h>
-#ifdef __FreeBSD__
+#if  defined(__FreeBSD__) || defined(__DragonFly__)
 #include <stdlib.h>
 #else
 #include <malloc.h>
@@ -456,7 +456,7 @@ static void init_userp (unsigned int buf
     }
     for (n_buffers = 0; n_buffers < 4; ++n_buffers) {
         buffers[n_buffers].length = buffer_size;
-#ifdef __FreeBSD__
+#if defined(__FreeBSD__) || defined(__DragonFly__)
 	if(posix_memalign(&buffers[n_buffers].start, page_size, buffer_size))
 	{
 #else
