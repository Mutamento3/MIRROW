import type {Message, ImageAttachment} from '../types';

export class OutgoingPreparationError extends Error {
  readonly cause: unknown;
  constructor(cause: unknown) {
    super('发送前准备未完成');
    this.name = 'OutgoingPreparationError';
    this.cause = cause;
  }
}

export function upsertOutgoingMessage(messages: Message[], message: Message): Message[] {
  return messages.some(item => item.id === message.id)
    ? messages.map(item => item.id === message.id ? message : item)
    : [...messages, message];
}

export function readImageData(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => typeof reader.result === 'string'
      ? resolve(reader.result) : reject(new Error('图片读取未完成'));
    reader.onerror = () => reject(reader.error || new Error('图片读取失败'));
    reader.onabort = () => reject(new Error('图片读取已取消'));
    reader.readAsDataURL(file);
  });
}

type DraftDependencies = {
  preview: (file: File) => string;
  release: (url: string) => void;
  read: (file: File) => Promise<string>;
};

export function createOutgoingDraft(message: Message, files: File[], dependencies: DraftDependencies = {
  preview: file => URL.createObjectURL(file),
  release: url => URL.revokeObjectURL(url),
  read: readImageData,
}) {
  const previews: ImageAttachment[] = [];
  try {
    files.forEach((file, index) => previews.push({
      id: `${message.id}-image-${index}`, url: dependencies.preview(file),
      filename: file.name, size: file.size, type: file.type,
    }));
  } catch (cause) {
    previews.forEach(image => dependencies.release(image.url));
    throw cause;
  }
  let released = false;
  return {
    message: files.length ? {...message, images: previews} : message,
    async encode(): Promise<Message> {
      if (!files.length) return message;
      const images = await Promise.all(files.map(async (file, index) => ({
        ...previews[index], url: await dependencies.read(file),
      })));
      return {...message, images};
    },
    release() {
      if (released) return;
      released = true;
      previews.forEach(image => dependencies.release(image.url));
    },
  };
}

/** The local row is published before any await. Only prepared data enters transport. */
export function prepareOutgoingMessage(draft: ReturnType<typeof createOutgoingDraft>, operations: {
  publish: (message: Message) => void;
  discard: (id: string) => void;
  preparing: (active: boolean) => void;
}): Promise<Message> {
  operations.publish(draft.message);
  operations.preparing(true);
  return (async () => {
    try {
      const ready = await draft.encode();
      operations.publish(ready);
      return ready;
    } catch (cause) {
      operations.discard(draft.message.id);
      throw new OutgoingPreparationError(cause);
    } finally {
      draft.release();
      operations.preparing(false);
    }
  })();
}
