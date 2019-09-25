--- /dev/null	2019-09-25 23:24:38.410299978 +0800
+++ lfshttp/certs_dragonfly.go	2019-09-25 23:11:11.817478000 +0800
@@ -0,0 +1,8 @@
+package lfshttp
+
+import "crypto/x509"
+
+func appendRootCAsForHostFromPlatform(pool *x509.CertPool, host string) *x509.CertPool {
+	// Do nothing, use golang default
+	return pool
+}
