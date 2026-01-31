--- boreas/ping.c.orig	2026-01-31 13:33:46 UTC
+++ boreas/ping.c
@@ -105,7 +105,7 @@ throttle (int soc, int so_sndbuf)
   int cur_so_sendbuf = -1;
 
   /* Get the current size of the output queue size */
-#ifdef __FreeBSD__
+#if defined(__FreeBSD__) || defined(__DragonFly__)
   if (ioctl (soc, TIOCOUTQ, &cur_so_sendbuf) == -1)
 #else
   if (ioctl (soc, SIOCOUTQ, &cur_so_sendbuf) == -1)
@@ -125,7 +125,7 @@ throttle (int soc, int so_sndbuf)
       while (cur_so_sendbuf >= so_sndbuf)
         {
           usleep (100000);
-#ifdef __FreeBSD__
+#if defined(__FreeBSD__) || defined(__DragonFly__)
           if (ioctl (soc, TIOCOUTQ, &cur_so_sendbuf) == -1)
 #else
           if (ioctl (soc, SIOCOUTQ, &cur_so_sendbuf) == -1)
@@ -209,9 +209,13 @@ send_icmp_v4 (int soc, struct in_addr *dst)
 
   int len;
   int datalen = 56;
-#ifdef __FreeBSD__
-  struct icmp *icmp;
-#else
+  #if defined(__DragonFly__)
+struct icmphdr { /* not available as partial struct from struct icmp */
+	u_char	icmp_type;	/* type of message, see below */
+	u_char	icmp_code;	/* type sub code */
+	u_short	icmp_cksum;
+};
+#endif 
   struct icmphdr *icmp;
 #endif
 
@@ -219,7 +223,7 @@ send_icmp_v4 (int soc, struct in_addr *dst)
   static int so_sndbuf = -1; // socket send buffer
   static int init = -1;
 
-#ifdef __FreeBSD__
+#if defined(__FreeBSD__) || defined(__DragonFly__)
   icmp = (struct icmp *) sendbuf;
   icmp->icmp_type = ICMP_ECHO;
   icmp->icmp_code = 0;
@@ -234,7 +238,7 @@ send_icmp_v4 (int soc, struct in_addr *dst)
 #endif
 
   len = 8 + datalen;
-#ifdef __FreeBSD__
+#if defined(__FreeBSD__) || defined(__DragonFly__)
   icmp->icmp_cksum = 0;
   icmp->icmp_cksum = in_cksum ((u_short *) icmp, len);
 #else
@@ -313,8 +317,8 @@ send_icmp (gpointer key, gpointer value, gpointer scan
         }
       else
         {
-#ifdef __FreeBSD__
-          dst4.s_addr = dst6_p->s6_addr[12];
+#if defined(__FreeBSD__) || defined(__DragonFly__)  
+	dst4.s_addr = dst6_p->s6_addr[12];
 #else
           dst4.s_addr = dst6_p->s6_addr32[3];
 #endif
@@ -582,7 +586,7 @@ send_tcp (gpointer key, gpointer value, gpointer scann
     }
   else
     {
-#ifdef __FreeBSD__
+#if defined(__FreeBSD__) || defined(__DragonFly__)
        dst4.s_addr = dst6_p->s6_addr[12];
 #else
        dst4.s_addr = dst6_p->s6_addr32[3];
