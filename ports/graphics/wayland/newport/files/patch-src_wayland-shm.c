--- src/wayland-shm.c.orig	2017-07-25 14:19:12.722864000 +0300
+++ src/wayland-shm.c	2017-07-25 14:48:35.482456000 +0300
@@ -30,6 +30,10 @@
 
 #define _GNU_SOURCE
 
+#if defined(__DragonFly__)
+#include <sys/param.h>
+#endif
+
 #include <stdbool.h>
 #include <stdio.h>
 #include <stdlib.h>
@@ -59,6 +63,9 @@
 	char *data;
 	int32_t size;
 	int32_t new_size;
+#if defined(__DragonFly__)
+	int fd;
+#endif
 };
 
 struct wl_shm_buffer {
@@ -84,7 +91,24 @@
 	if (pool->size == pool->new_size)
 		return;
 
-	data = mremap(pool->data, pool->size, pool->new_size, MREMAP_MAYMOVE);
+#if defined(__DragonFly__)
+	int32_t osize = (pool->size + PAGE_SIZE - 1) & ~PAGE_MASK;
+	if (pool->new_size <= osize) {
+		pool->size = pool->new_size;
+		return;
+	}
+	data = mmap(pool->data + osize, pool->new_size - osize, PROT_READ,
+	    MAP_SHARED | MAP_TRYFIXED, pool->fd, osize);
+	if (data == MAP_FAILED) {
+		munmap(pool->data, pool->size);
+		data = mmap(NULL, pool->new_size, PROT_READ, MAP_SHARED, pool->fd, 0);
+	} else {
+		pool->size = pool->new_size;
+		return;
+	}
+#else
+ 	data = mremap(pool->data, pool->size, size, MREMAP_MAYMOVE);
+#endif
 	if (data == MAP_FAILED) {
 		wl_resource_post_error(pool->resource,
 				       WL_SHM_ERROR_INVALID_FD,
@@ -111,6 +135,9 @@
 		return;
 
 	munmap(pool->data, pool->size);
+#if defined(__DragonFly__)
+	close(pool->fd);
+#endif
 	free(pool);
 }
 
@@ -235,6 +262,8 @@
 				       "shrinking pool invalid");
 		return;
 	}
+	if (size == pool->size)
+		return;
 
 	pool->new_size = size;
 
@@ -276,21 +305,27 @@
 	pool->external_refcount = 0;
 	pool->size = size;
 	pool->new_size = size;
-	pool->data = mmap(NULL, size,
-			  PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
+	pool->data = mmap(NULL, size, PROT_READ, MAP_SHARED, fd, 0);
 	if (pool->data == MAP_FAILED) {
 		wl_resource_post_error(resource,
 				       WL_SHM_ERROR_INVALID_FD,
 				       "failed mmap fd %d", fd);
 		goto err_free;
 	}
-	close(fd);
+#if defined(__DragonFly__)
+	pool->fd = fd;
+#else
+ 	close(fd);
+#endif
 
 	pool->resource =
 		wl_resource_create(client, &wl_shm_pool_interface, 1, id);
 	if (!pool->resource) {
 		wl_client_post_no_memory(client);
 		munmap(pool->data, pool->size);
+#if defined(__DragonFly__)
+		close(fd);
+#endif
 		free(pool);
 		return;
 	}
@@ -495,6 +530,14 @@
 	sigbus_data->fallback_mapping_used = 1;
 
 	/* This should replace the previous mapping */
+#if defined(__DragonFly__)
+	if (mmap(pool->data, pool->size,
+		 PROT_READ, MAP_PRIVATE | MAP_FIXED | MAP_ANON,
+		 0, 0) == MAP_FAILED) {
+		reraise_sigbus();
+		return;
+	}
+#else
 	if (mmap(pool->data, pool->size,
 		 PROT_READ | PROT_WRITE,
 		 MAP_PRIVATE | MAP_FIXED | MAP_ANONYMOUS,
@@ -502,6 +545,7 @@
 		reraise_sigbus();
 		return;
 	}
+#endif
 }
 
 static void
