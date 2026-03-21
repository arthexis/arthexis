package body Apps.Core.Views.Core_Views is

   function App_Registry_View_SQL return String is
   begin
      return
        "SELECT id, app_name, enabled "
        & "FROM core_app_registry "
        & "ORDER BY app_name";
   end App_Registry_View_SQL;

end Apps.Core.Views.Core_Views;
