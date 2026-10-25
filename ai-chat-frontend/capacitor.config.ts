import type { CapacitorConfig } from '@capacitor/cli';

const config: CapacitorConfig = {
  appId: 'com.mirrow.app',
  appName: 'MIRROW',
  webDir: 'dist',
  server: {
    androidScheme: 'http',
  },
};

export default config;
