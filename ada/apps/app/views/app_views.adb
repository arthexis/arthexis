package body Apps.App.Views.App_Views is

   function App_Matrix_SQL return String is
   begin
      return
        "SELECT id, app_name, component_kind, is_optional "
        & "FROM app_app "
        & "ORDER BY component_kind, app_name";
   end App_Matrix_SQL;

end Apps.App.Views.App_Views;
