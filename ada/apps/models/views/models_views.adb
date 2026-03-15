package body Apps.Models.Views.Models_Views is

   function Model_Index_SQL return String is
   begin
      return
        "SELECT id, app_name, table_name "
        & "FROM models_model "
        & "ORDER BY app_name, table_name";
   end Model_Index_SQL;

end Apps.Models.Views.Models_Views;
