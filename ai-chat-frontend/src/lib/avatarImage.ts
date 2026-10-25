const AVATAR_MAX_BYTES = 2 * 1024 * 1024;
const AVATAR_SOURCE_MAX_BYTES = 30 * 1024 * 1024;

function readAsDataUrl(file: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error('图片读取失败'));
    reader.onload = () => resolve(String(reader.result || ''));
    reader.readAsDataURL(file);
  });
}

function loadImage(source: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.onload = () => resolve(image);
    image.onerror = () => reject(new Error('无法解析这张图片'));
    image.src = source;
  });
}

function dataUrlBytes(value: string): number {
  const comma = value.indexOf(',');
  const payload = comma >= 0 ? value.slice(comma + 1) : value;
  return Math.ceil(payload.length * 3 / 4);
}

/** 超过 2MB 的头像在本机降采样；不上传原图，也不改变小图格式。 */
export async function prepareAvatarImage(file: File): Promise<string> {
  if (!file.type.startsWith('image/')) throw new Error('请选择图片文件');
  if (file.size > AVATAR_SOURCE_MAX_BYTES) throw new Error('原图不能超过 30MB');

  const source = await readAsDataUrl(file);
  if (file.size <= AVATAR_MAX_BYTES) return source;

  const image = await loadImage(source);
  let scale = Math.min(1, 1280 / Math.max(image.naturalWidth, image.naturalHeight));
  const canvas = document.createElement('canvas');
  const context = canvas.getContext('2d');
  if (!context) throw new Error('当前设备无法压缩图片');

  for (let round = 0; round < 7; round += 1) {
    canvas.width = Math.max(1, Math.round(image.naturalWidth * scale));
    canvas.height = Math.max(1, Math.round(image.naturalHeight * scale));
    context.clearRect(0, 0, canvas.width, canvas.height);
    context.drawImage(image, 0, 0, canvas.width, canvas.height);
    for (const quality of [0.9, 0.8, 0.7, 0.6, 0.5]) {
      const compressed = canvas.toDataURL('image/webp', quality);
      if (dataUrlBytes(compressed) <= AVATAR_MAX_BYTES) return compressed;
    }
    scale *= 0.78;
  }
  throw new Error('图片压缩后仍超过 2MB，请换一张尺寸更小的图片');
}
