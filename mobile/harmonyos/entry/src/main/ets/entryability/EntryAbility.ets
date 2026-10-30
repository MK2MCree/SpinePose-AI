import { UIAbility } from '@kit.AbilityKit';
import { window } from '@kit.ArkUI';
import { hilog } from '@kit.PerformanceAnalysisKit';

export default class EntryAbility extends UIAbility {
  onWindowStageCreate(windowStage: window.WindowStage): void {
    try {
      windowStage.getMainWindow().then((mainWindow: window.Window): void => {
        mainWindow.setWindowSystemBarProperties({
          statusBarColor: '#F8F7FC', statusBarContentColor: '#262338',
          navigationBarColor: '#FFFFFF', navigationBarContentColor: '#262338'
        }).catch((): void => {
          hilog.warn(0x0000, 'SpineInsight', 'Unable to update system bar appearance.');
        });
      }).catch((): void => {
        hilog.warn(0x0000, 'SpineInsight', 'Unable to access the main window.');
      });
      windowStage.loadContent('pages/Index', (error) => {
        if (error.code) {
          hilog.error(0x0000, 'SpineInsight', 'Unable to load the main page.');
        }
      });
    } catch (error) {
      hilog.error(0x0000, 'SpineInsight', 'Unable to initialize the main page.');
    }
  }
}
