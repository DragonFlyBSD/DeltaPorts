--- src/vendor/openssl-sys/src/ossl111.rs.orig	2018-04-02 18:34:05 UTC
+++ src/vendor/openssl-sys/src/ossl111.rs
@@ -0,0 +1,84 @@
+use libc::{c_char, c_uchar, c_int, c_uint, c_ulong, size_t, c_void};
+
+pub type SSL_CTX_keylog_cb_func =
+    Option<unsafe extern "C" fn(ssl: *const ::SSL, line: *const c_char)>;
+
+pub type SSL_custom_ext_add_cb_ex =
+    Option<unsafe extern "C" fn(ssl: *mut ::SSL, ext_type: c_uint,
+                                context: c_uint,
+                                out: *mut *const c_uchar,
+                                outlen: *mut size_t, x: *mut ::X509,
+                                chainidx: size_t, al: *mut c_int,
+                                add_arg: *mut c_void) -> c_int>;
+
+pub type SSL_custom_ext_free_cb_ex =
+    Option<unsafe extern "C" fn(ssl: *mut ::SSL, ext_type: c_uint,
+                                context: c_uint,
+                                out: *mut *const c_uchar,
+                                add_arg: *mut c_void)>;
+
+pub type SSL_custom_ext_parse_cb_ex =
+    Option<unsafe extern "C" fn(ssl: *mut ::SSL, ext_type: c_uint,
+                                context: c_uint,
+                                input: *const c_uchar,
+                                inlen: size_t, x: *mut ::X509,
+                                chainidx: size_t, al: *mut c_int,
+                                parse_arg: *mut c_void) -> c_int>;
+
+pub const SSL_COOKIE_LENGTH: c_int = 4096;
+
+pub const SSL_OP_ENABLE_MIDDLEBOX_COMPAT: c_ulong = 0x00100000;
+
+pub const TLS1_3_VERSION: c_int = 0x304;
+
+pub const SSL_EXT_TLS_ONLY: c_uint = 0x0001;
+/* This extension is only allowed in DTLS */
+pub const SSL_EXT_DTLS_ONLY: c_uint = 0x0002;
+/* Some extensions may be allowed in DTLS but we don't implement them for it */
+pub const SSL_EXT_TLS_IMPLEMENTATION_ONLY: c_uint = 0x0004;
+/* Most extensions are not defined for SSLv3 but EXT_TYPE_renegotiate is */
+pub const SSL_EXT_SSL3_ALLOWED: c_uint = 0x0008;
+/* Extension is only defined for TLS1.2 and below */
+pub const SSL_EXT_TLS1_2_AND_BELOW_ONLY: c_uint = 0x0010;
+/* Extension is only defined for TLS1.3 and above */
+pub const SSL_EXT_TLS1_3_ONLY: c_uint = 0x0020;
+/* Ignore this extension during parsing if we are resuming */
+pub const SSL_EXT_IGNORE_ON_RESUMPTION: c_uint = 0x0040;
+pub const SSL_EXT_CLIENT_HELLO: c_uint = 0x0080;
+/* Really means TLS1.2 or below */
+pub const SSL_EXT_TLS1_2_SERVER_HELLO: c_uint = 0x0100;
+pub const SSL_EXT_TLS1_3_SERVER_HELLO: c_uint = 0x0200;
+pub const SSL_EXT_TLS1_3_ENCRYPTED_EXTENSIONS: c_uint = 0x0400;
+pub const SSL_EXT_TLS1_3_HELLO_RETRY_REQUEST: c_uint = 0x0800;
+pub const SSL_EXT_TLS1_3_CERTIFICATE: c_uint = 0x1000;
+pub const SSL_EXT_TLS1_3_NEW_SESSION_TICKET: c_uint = 0x2000;
+pub const SSL_EXT_TLS1_3_CERTIFICATE_REQUEST: c_uint = 0x4000;
+
+
+extern "C" {
+    pub fn SSL_CTX_set_keylog_callback(ctx: *mut ::SSL_CTX, cb: SSL_CTX_keylog_cb_func);
+    pub fn SSL_CTX_add_custom_ext(ctx: *mut ::SSL_CTX, ext_type: c_uint, context: c_uint,
+                                  add_cb: SSL_custom_ext_add_cb_ex,
+                                  free_cb: SSL_custom_ext_free_cb_ex,
+                                  add_arg: *mut c_void,
+                                  parse_cb: SSL_custom_ext_parse_cb_ex,
+                                  parse_arg: *mut c_void) -> c_int;
+    pub fn SSL_stateless(s: *mut ::SSL) -> c_int;
+    pub fn SSL_CIPHER_get_handshake_digest(cipher: *const ::SSL_CIPHER) -> *const ::EVP_MD;
+    pub fn SSL_CTX_set_stateless_cookie_generate_cb(
+        s: *mut ::SSL_CTX,
+        cb: Option<unsafe extern "C" fn(
+            ssl: *mut ::SSL,
+            cookie: *mut c_uchar,
+            cookie_len: *mut size_t
+        ) -> c_int>
+    );
+    pub fn SSL_CTX_set_stateless_cookie_verify_cb(
+        s: *mut ::SSL_CTX,
+        cb: Option<unsafe extern "C" fn(
+            ssl: *mut ::SSL,
+            cookie: *const c_uchar,
+            cookie_len: size_t
+        ) -> c_int>
+    );
+}
