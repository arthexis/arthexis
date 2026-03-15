package body Apps.Model.Views.Model_Views is

   function Model_Matrix_SQL return String is
   begin
      return
        "SELECT id, app_name, model_name, is_optional "
        & "FROM model_model "
        & "ORDER BY app_name, model_name";
   end Model_Matrix_SQL;

end Apps.Model.Views.Model_Views;
