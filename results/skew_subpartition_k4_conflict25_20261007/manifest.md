# Skew Subpartition Cohort

- Cohort: conflict25_20261007
- Started: 2026-10-07T17:20:51+08:00
- k: 4
- Threshold: 1.7
- Split factor: 4
- Injected conflict rate: 0.25
- Conflict RNG seed: 42
- Git HEAD: e5d245bcd241dabbc125bdc6a154bf575dcad3ed
- Worktree: modified
- Runtime: Linux DESKTOP-9QMHHN7 6.18.40.1-microsoft-standard-WSL2 #1 SMP PREEMPT_DYNAMIC Fri Jul 31 22:12:15 UTC 2026 x86_64 x86_64 x86_64 GNU/Linux
- Logical CPUs: 16
- Memory:
              total        used        free      shared  buff/cache   available
Mem:           23Gi       2.1Gi        15Gi       5.0Mi       6.1Gi        21Gi
Swap:         6.0Gi          0B       6.0Gi
- Python: Python 3.13.12
- Java: openjdk version "21.0.7" 2025-04-15
- Docker: 28.1.1
- Compose: 2.35.1
- Worker image: sha256:02d9503191e9a49b35d03647d2f004a72c1699168a20919995108e163f270d62
- Running services:
NAME          IMAGE            COMMAND                  SERVICE   CREATED          STATUS          PORTS
kg-redis-1    redis:7-alpine   "docker-entrypoint.s…"   redis     54 seconds ago   Up 53 seconds   127.0.0.1:6379->6379/tcp
kg-worker-1   kg-worker        "java -Xmx1750m -Dja…"   worker    54 seconds ago   Up 52 seconds
kg-worker-2   kg-worker        "java -Xmx1750m -Dja…"   worker    54 seconds ago   Up 52 seconds
kg-worker-3   kg-worker        "java -Xmx1750m -Dja…"   worker    54 seconds ago   Up 52 seconds
kg-worker-4   kg-worker        "java -Xmx1750m -Dja…"   worker    54 seconds ago   Up 52 seconds
- Input hashes:
63b9574a84cb581ec97be4267d38c2fb9ceb82b7b4b47b56c86e3c8d1ed36ce9  data/urbanos_before/stop_times.txt
b00e5ffa130f110e786d2f7d32241953c096315e63e38023e105b87212b40d15  results/baseline_graph_urbanos.nq
608a4aeb87eab936a40defbbee2241fcce049110a31c98632951efab23392869  delta_mapping_base.ttl
819371d49ca47d8ffddae0f34e95f38e8eaaf588ee023e3c2c7527a14d302f58  rmlmapper.jar
- Seed snapshot hashes:
59ed115a295de3ff0229548a544f9409e4deabf77c3ba7a92ef62782b01a73c8  data/urbanos_skew_k4_p000_s1/stop_times.txt
94a86cbd5b3dd49972051241f1daf1702481ae4446012437c0ffc41e3b3765d2  data/urbanos_skew_k4_p000_s2/stop_times.txt
d73adca36450315a5a6cd8dd186fa42a2687d1ae873171b6e3ddc3491ca5a50b  data/urbanos_skew_k4_p000_s3/stop_times.txt
5ce8107002b53a642518c404247022f9e21b83fb075054cdf1e98fe28ecfdd44  data/urbanos_skew_k4_p000_s4/stop_times.txt
c014b2860a2a1e18f445ef77d1454b176e08ac3c359560daae60a476902ed088  data/urbanos_skew_k4_p000_s5/stop_times.txt
153f51009baf539a2e57dd66babc675bc6c435306e46a543f3f46f4583048996  data/urbanos_skew_k4_p025_s1/stop_times.txt
bf48205200d6763024358741e03b2896863f09ec768b7c18ad41e0dcb9919bf4  data/urbanos_skew_k4_p025_s2/stop_times.txt
870ee36916237b3c06b8d9d4a7c0ce9d1cf66a3cf01d7f970910f119797ae29d  data/urbanos_skew_k4_p025_s3/stop_times.txt
21d7225650dcfe74ebb3178029a53dc58c8b6f32f365f65a5a1b2be32f4787ac  data/urbanos_skew_k4_p025_s4/stop_times.txt
8681be5c3033b67ea6eda9643a15a2117ac3cba9c0304afe7a4f6738a016bba8  data/urbanos_skew_k4_p025_s5/stop_times.txt
f75b32ec3df6f6f5be391563d6dcfbbd4682ed60fe64b8851643d612c002e058  data/urbanos_skew_k4_p050_s1/stop_times.txt
34dca634d4a69097ec8d7c8a892d5e6d3f15f42bfacc6094fe961bd18730ce75  data/urbanos_skew_k4_p050_s2/stop_times.txt
ae51b39b26e6afc2b8013321d66905cd6e77eea0310b62737cce9070d5c9d29a  data/urbanos_skew_k4_p050_s3/stop_times.txt
d3061c8e9ba21685af1f61ff312618089d686c2cd2f668e337295e33e40ea882  data/urbanos_skew_k4_p050_s4/stop_times.txt
aa1ce4e3ca85fd6153deb01b560906047bdc9e55b906ad2d1ad982b91cb4e739  data/urbanos_skew_k4_p050_s5/stop_times.txt
131891443d655930d48554f3a3f31526022d076f669d492c4956703fcd961b51  data/urbanos_skew_k4_p075_s1/stop_times.txt
ec928955e30c69ad1188c1bd8d2c8a62f8f1a42d5e0a5abb5e7a113f070f50b4  data/urbanos_skew_k4_p075_s2/stop_times.txt
f25b24c9ab3d02c699cc4f9c84504e7a02be3f653896ecc5bb518a7484a3e4a4  data/urbanos_skew_k4_p075_s3/stop_times.txt
83e7655cb9079e9c56bd288e19eeef457ce6d8e25a893aab2cc6c267b62b85bd  data/urbanos_skew_k4_p075_s4/stop_times.txt
045c358af938c369f763270b73a421bf5d11c4c3637ee482d58bc62331bd4bc7  data/urbanos_skew_k4_p075_s5/stop_times.txt
7f7e47c8f615ac8e8a49ccb699c8b4fa0d06d75a057848ed2c29393dfebf50d7  data/urbanos_skew_k4_p100_s1/stop_times.txt
dbc45a28b4da00acd6716060eb8082c9455acd7f839d00fd7804c3904acb326f  data/urbanos_skew_k4_p100_s2/stop_times.txt
47594b22f538561a6900315cd5951c02aabfb693946fcb8960f8ee6039d02167  data/urbanos_skew_k4_p100_s3/stop_times.txt
2425e6467c09d406e9e1c451dcfeac9cee2b49a8f68037362aed677bd1d23908  data/urbanos_skew_k4_p100_s4/stop_times.txt
f6cdc49cd36559945e10dc2800056fcefdabe7c9e3f68e020f8da6b831dad606  data/urbanos_skew_k4_p100_s5/stop_times.txt
- Finished: 2026-10-07T17:29:39+08:00
- Code hashes:
89241e505148bcfc9d890fee36c2c07a3b5cfa085346e286e1a7d5924377bb0e  run_distributed_update.py
a9a3e8dde136905fabc4deb976b27dbf208640bece91582e6161f66ba677a757  ablation/run_skew_subpartition_matrix.sh
a8597cce4a00d2e5203ba71b3cc860cca061e52f5270bed3febff609d0bcfae8  ablation/summarize_skew_subpartition.py
88c235ec10322d17cd2d8d09c2b2d50496f2c738bff8032a3874085eb1413c8e  partitioning.py
1d3aa4fb33c87607fa8bdeebc59562e93160bcdb83fb030e1110431ba4c72daf  dist/src/Worker.java
