package body Apps.Functions.Views.Functions_Views is

   function Function_Index_SQL return String is
   begin
      return
        "SELECT id, app_name, function_name "
        & "FROM functions_function "
        & "ORDER BY app_name, function_name";
   end Function_Index_SQL;

end Apps.Functions.Views.Functions_Views;
