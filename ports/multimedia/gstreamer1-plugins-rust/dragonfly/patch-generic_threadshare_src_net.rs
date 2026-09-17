--- generic/threadshare/src/net.rs.orig
+++ generic/threadshare/src/net.rs
@@ -54,6 +54,13 @@
     ) -> Result<(), io::Error> {
         let index = iface.index.unwrap_or(0);
 
+        #[cfg(any(target_os = "openbsd", target_os = "dragonfly"))]
+        let ip_addr = match &iface.address {
+            getifaddrs::Address::V4(ifaddr) => ifaddr.address,
+            getifaddrs::Address::V6(_) => return Err(io::Error::other("Interface address is IPv6")),
+            getifaddrs::Address::Mac(_) => return Err(io::Error::other("Interface address is Mac")),
+        };
+
         #[cfg(not(any(
             target_os = "solaris",
             target_os = "illumos",
