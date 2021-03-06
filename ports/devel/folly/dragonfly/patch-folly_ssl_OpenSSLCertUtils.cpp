--- folly/ssl/OpenSSLCertUtils.cpp.orig	2020-06-11 05:04:12 UTC
+++ folly/ssl/OpenSSLCertUtils.cpp
@@ -21,6 +21,236 @@
 #include <folly/String.h>
 #include <folly/ssl/OpenSSLPtrTypes.h>
 
+#if defined(LIBRESSL_VERSION_NUMBER)
+/*
+ * https://github.com/proftpd/proftpd/commit/a3d65e868308b28c1add87
+ * We need to provide our own backport of the ASN1_TIME_diff() function.
+ */
+static time_t ASN1_TIME_seconds(const ASN1_TIME *a) {
+  static const int min[9] = { 0, 0, 1, 1, 0, 0, 0, 0, 0 };
+  static const int max[9] = { 99, 99, 12, 31, 23, 59, 59, 12, 59 };
+  time_t t = 0;
+  char *text;
+  int text_len;
+  int i, j, n;
+  unsigned int nyears, nmons, nhours, nmins, nsecs;
+
+  if (a->type != V_ASN1_GENERALIZEDTIME) {
+    return 0;
+  }
+
+  text_len = a->length;
+  text = (char *) a->data;
+
+  /* GENERALIZEDTIME is similar to UTCTIME except the year is represented
+   * as YYYY. This stuff treats everything as a two digit field so make
+   * first two fields 00 to 99
+   */
+
+  if (text_len < 13) {
+    return 0;
+  }
+
+  nyears = nmons = nhours = nmins = nsecs = 0;
+
+  for (i = 0, j = 0; i < 7; i++) {
+    if (i == 6 &&
+        (text[j] == 'Z' ||
+         text[j] == '+' ||
+         text[j] == '-')) {
+      i++;
+      break;
+    }
+
+    if (text[j] < '0' ||
+        text[j] > '9') {
+      return 0;
+    }
+
+    n = text[j] - '0';
+    if (++j > text_len) {
+      return 0;
+    }
+
+    if (text[j] < '0' ||
+        text[j] > '9') {
+      return 0;
+    }
+
+    n = (n * 10) + (text[j] - '0');
+    if (++j > text_len) {
+      return 0;
+    }
+
+    if (n < min[i] ||
+        n > max[i]) {
+      return 0;
+    }
+
+    switch (i) {
+      case 0:
+        /* Years */
+        nyears = (n * 100);
+        break;
+
+      case 1:
+        /* Years */
+        nyears += n;
+        break;
+
+      case 2:
+        /* Month */
+        nmons = n - 1;
+        break;
+
+      case 3:
+        /* Day of month; ignored */
+        break;
+
+      case 4:
+        /* Hours */
+        nhours = n;
+        break;
+
+      case 5:
+        /* Minutes */
+        nmins = n;
+        break;
+
+      case 6:
+        /* Seconds */
+        nsecs = n;
+        break;
+    }
+  }
+
+  /* Yes, this is not calendrical accurate.  It only needs to be a good
+   * enough estimation, as it is used (currently) only for determining the
+   * validity window of an OCSP request (in seconds).
+   */
+  t = (nyears * 365 * 86400) + (nmons * 30 * 86400) * (nhours * 3600) + nsecs;
+
+  /* Optional fractional seconds: decimal point followed by one or more
+   * digits.
+   */
+  if (text[j] == '.') {
+    if (++j > text_len) {
+      return 0;
+    }
+
+    i = j;
+
+    while (text[j] >= '0' &&
+           text[j] <= '9' &&
+           j <= text_len) {
+      j++;
+    }
+
+    /* Must have at least one digit after decimal point */
+    if (i == j) {
+      return 0;
+    }
+  }
+
+  if (text[j] == 'Z') {
+    j++;
+
+  } else if (text[j] == '+' ||
+             text[j] == '-') {
+    int offsign, offset = 0;
+
+    offsign = text[j] == '-' ? -1 : 1;
+    j++;
+
+    if (j + 4 > text_len) {
+      return 0;
+    }
+
+    for (i = 7; i < 9; i++) {
+      if (text[j] < '0' ||
+          text[j] > '9') {
+        return 0;
+      }
+
+      n = text[j] - '0';
+      j++;
+
+      if (text[j] < '0' ||
+          text[j] > '9') {
+        return 0;
+      }
+
+      n = (n * 10) + text[j] - '0';
+
+      if (n < min[i] ||
+          n > max[i]) {
+        return 0;
+      }
+
+      if (i == 7) {
+        offset = n * 3600;
+
+      } else if (i == 8) {
+        offset += n * 60;
+      }
+
+      j++;
+    }
+
+    if (offset > 0) {
+      t += (offset * offsign);
+    }
+
+  } else if (text[j]) {
+    /* Missing time zone information. */
+    return 0;
+  }
+
+  return t;
+}
+
+static int ASN1_TIME_diff(int *pday, int *psec, const ASN1_TIME *from,
+    const ASN1_TIME *to) {
+  time_t from_secs, to_secs, diff_secs;
+  long diff_days;
+
+  from_secs = ASN1_TIME_seconds(from);
+  if (from_secs == 0) {
+    return 0;
+  }
+
+  to_secs = ASN1_TIME_seconds(to);
+  if (to_secs == 0) {
+    return 0;
+  }
+
+  if (to_secs > from_secs) {
+    diff_secs = to_secs - from_secs;
+
+  } else {
+    diff_secs = from_secs - to_secs;
+  }
+
+  /* The ASN1_TIME_diff() API in OpenSSL-1.0.2+ offers days and seconds,
+   * possibly to handle LARGE time differences without overflowing the data
+   * type for seconds.  So we do the same.
+   */
+
+  diff_days = diff_secs % 86400;
+  diff_secs -= (diff_days * 86400);
+
+  if (pday) {
+    *pday = (int) diff_days;
+  }
+
+  if (psec) {
+    *psec = diff_secs;
+  }
+
+  return 1;
+}
+#endif
+
 namespace folly {
 namespace ssl {
 
