/**
 * 纯映射耗时基准：同JVM内先预热一次（类加载等一次性成本），再计时5次取中位数。
 * 与worker的稳态行为一致（常驻JVM复用），测得的是"每增加一行数据的边际映射成本"。
 */
import java.io.File;
import java.util.Arrays;

public class MappingBench {
    public static void main(String[] args) throws Exception {
        for (int i = 0; i + 1 < args.length; i += 2) {
            String mapping = args[i], out = args[i + 1];
            new File(out).delete();
            be.ugent.rml.cli.Main.main(new String[]{
                    "-m", mapping, "-o", out, "-s", "nquads"}); // 预热
            long[] t = new long[5];
            for (int r = 0; r < 5; r++) {
                long t0 = System.currentTimeMillis();
                be.ugent.rml.cli.Main.main(new String[]{
                        "-m", mapping, "-o", out, "-s", "nquads"});
                t[r] = System.currentTimeMillis() - t0;
            }
            Arrays.sort(t);
            System.out.println("BENCH " + mapping + " median=" + t[2]
                    + "ms all=" + Arrays.toString(t));
        }
        System.out.println("BENCH-END");
    }
}
