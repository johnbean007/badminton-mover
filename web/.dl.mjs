import { S3Client, GetObjectCommand } from "@aws-sdk/client-s3";
import { writeFile } from "node:fs/promises";
const r2 = new S3Client({ region: "auto", endpoint: `https://${process.env.R2_ACCOUNT_ID}.r2.cloudflarestorage.com`, credentials: { accessKeyId: process.env.R2_ACCESS_KEY_ID, secretAccessKey: process.env.R2_SECRET_ACCESS_KEY } });
const [key, dest] = process.argv.slice(2);
try {
  const res = await r2.send(new GetObjectCommand({ Bucket: process.env.R2_BUCKET, Key: key }));
  await writeFile(dest, Buffer.from(await res.Body.transformToByteArray()));
  console.log("saved", key);
} catch (e) { console.log("failed", key, e.name); }
