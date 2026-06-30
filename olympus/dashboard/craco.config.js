const CopyWebpackPlugin = require('copy-webpack-plugin');
const path = require('path');

module.exports = {
  webpack: {
    configure: (webpackConfig) => {
      // Add Cesium source code handling
      webpackConfig.module.rules.push({
        test: /\.js$/,
        include: path.resolve(__dirname, 'node_modules/cesium/Source'),
        use: {
          loader: require.resolve('@open-wc/webpack-import-meta-loader'),
        },
      });

      // Copy Cesium assets
      webpackConfig.plugins.push(
        new CopyWebpackPlugin({
          patterns: [
            {
              from: path.join(__dirname, 'node_modules/cesium/Build/Cesium/Workers'),
              to: 'cesium/Workers',
            },
            {
              from: path.join(__dirname, 'node_modules/cesium/Build/Cesium/ThirdParty'),
              to: 'cesium/ThirdParty',
            },
            {
              from: path.join(__dirname, 'node_modules/cesium/Build/Cesium/Assets'),
              to: 'cesium/Assets',
            },
            {
              from: path.join(__dirname, 'node_modules/cesium/Build/Cesium/Widgets'),
              to: 'cesium/Widgets',
            },
          ],
        })
      );

      // Define Cesium base URL
      webpackConfig.plugins.push(
        new (require('webpack').DefinePlugin)({
          CESIUM_BASE_URL: JSON.stringify('/cesium'),
        })
      );

      return webpackConfig;
    },
  },
};
