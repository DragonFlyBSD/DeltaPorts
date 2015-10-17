--- src/device.c.orig	2015-10-17 14:32:38 +0200
+++ src/device.c
@@ -187,6 +187,87 @@
 #endif /* defined(HAVE_LIBPROCSTAT_H) */
 }
 
+int
+devq_device_get_name_from_fd(int fd,
+    char *name, size_t *name_len)
+{
+#if !defined(HAVE_LIBPROCSTAT_H)
+	int ret, found;
+	DIR *dir;
+	struct stat st;
+	struct dirent *dp;
+	char tmp_path[256];
+	size_t tmp_path_len;
+
+	/*
+	 * FIXME: This function is specific to DRM devices.
+	 */
+#define DEVQ_DRIDEV_DIR "/dev/dri"
+
+	ret = fstat(fd, &st);
+	if (ret != 0)
+		return (-1);
+	if (!S_ISCHR(st.st_mode)) {
+		errno = EBADF;
+		return (-1);
+	}
+
+	dir = opendir(DEVQ_DRIDEV_DIR);
+	if (dir == NULL)
+		return (-1);
+
+	found = 0;
+	while ((dp = readdir(dir)) != NULL) {
+		struct stat tmp_st;
+
+		if (dp->d_name[0] == '.')
+			continue;
+
+		tmp_path_len = strlen(DEVQ_DRIDEV_DIR);
+		strcpy(tmp_path, DEVQ_DRIDEV_DIR);
+		tmp_path[tmp_path_len++] = '/';
+		tmp_path[tmp_path_len] = '\0';
+
+		strcpy(tmp_path + tmp_path_len, dp->d_name);
+		tmp_path_len += dp->d_namlen;
+		tmp_path[tmp_path_len] = '\0';
+
+		ret = stat(tmp_path, &tmp_st);
+		if (ret != 0)
+			continue;
+
+		if (st.st_dev  == tmp_st.st_dev &&
+		    st.st_ino  == tmp_st.st_ino) {
+			found = 1;
+			break;
+		}
+	}
+
+	closedir(dir);
+
+	if (!found) {
+		errno = EBADF;
+		return -(1);
+	}
+
+	if (name) {
+		if (*name_len < tmp_path_len) {
+			*name_len = tmp_path_len;
+			errno = ENOMEM;
+			return (-1);
+		}
+
+		/* Cut off the initial "/dev/" part */
+		memcpy(name, tmp_path+5, tmp_path_len-5);
+	}
+	/* Ignore the initial "/dev/" part */
+	if (name_len)
+		*name_len = tmp_path_len-5;
+
+	return (0);
+#endif /* !defined(HAVE_LIBPROCSTAT_H) */
+}
+
 static int
 devq_compare_vgapci_busaddr(int i, int *domain, int *bus, int *slot,
     int *function)
